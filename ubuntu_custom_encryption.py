#!/usr/bin/python3
"""
Automate instructions on
https://help.ubuntu.com/community/ManualFullSystemEncryption
for our specific setup:
    * 256 GB SSD - Windows/Ubuntu system
    * 2 TB HD - Windows/Ubuntu data

Tested against 18.04 minimal installation.

Diverged from instructions:
    * No home directory encryption, not supported in Bionic

TODO:
    * Implemente all suspend functionality changes
    * Check fsstab
"""
import argparse
import difflib
import json
import os
from pathlib import Path
from pprint import pprint
import subprocess
import sys


# One-to-one mapping to our Physical Devices and Volume Group
LUKS_DEVICES = {
    # partition: name
    "/dev/sda5": "system",
    "/dev/sdb3": "data"
}
LUKS_NAME_MAP = dict([(value, key) for key, value in LUKS_DEVICES.items()])


VOLUME_GROUPS = {
        # logical volume: physical volumes
        "system": ["/dev/mapper/system"],
        "data": ["/dev/mapper/data"]
}


LOGICAL_VOLUMES = {
        # volume group
        "system": {
            # volume name
            "boot": {
                "size": ["--size", "1024M"],
                "type": "ext3",
                "filesystem_command": ["mkfs.ext3", "-L", "boot"]
            },
            # still need swap partition for LVM volumes, even in Bionic
            "swap": {
                "size": ["--size", "24576M"],
                "type": "swap",
                "filesystem_command": ["mkswap", "--label=swap"],
            },
            "root": {
                "size": ["--extents=100%FREE"],
                "type": "ext4",
                "filesystem_command": ["mkfs.ext4", "-L", "root"],
            },
       },
        "data": {
            "home": {
                "size": ["--extents=100%FREE"],
                "type": "ext4",
                "filesystem_command": ["mkfs.ext4", "-L", "home"]
            }
        }
}

GET_INIT_SCRIPT = """
# File:
#       /lib/cryptsetup/scripts/getinitramfskey.sh
#
# Description:
#       Called by initramfs using busybox ash to obtain the decryption key for the system.
#
# Purpose:
#       Used with loadinitramfskey.sh in full disk encryption to decrypt the system LUKS partition,
#       to prevent being asked twice for the same passphrase.

KEY="${1}"

if [ -f "${KEY}" ]
then
        cat "${KEY}"
else
        PASS=/bin/plymouth ask-for-password --prompt="Key not found. Enter LUKS Password: "
        echo "${PASS}"
fi

#<<EOF
"""

LOAD_INIT_SCRIPT = """
# File:
#       /etc/initramfs-tools/hooks/loadinitramfskey.sh
#
# Description:
#       Called by update-initramfs and loads getinitramfskey.sh to obtain the system decryption key.
#
# Purpose:
#       Used with getinitramfskey.sh in full disk encryption to decrypt the system LUKS partition,
#       to prevent being asked twice for the same passphrase.

PREREQ=""

prereqs()
{
        echo "${PREREQ}"
}

case "${1}" in
        prereqs)
                prereqs
                exit 0
        ;;
esac

. "${CONFDIR}"/initramfs.conf

. /usr/share/initramfs-tools/hook-functions

if [ ! -f "${DESTDIR}"/lib/cryptsetup/scripts/getinitramfskey.sh ]
then
        if [ ! -d "${DESTDIR}"/lib/cryptsetup/scripts/ ]
        then
                mkdir --parents "${DESTDIR}"/lib/cryptsetup/scripts/
        fi
        cp /lib/cryptsetup/scripts/getinitramfskey.sh "${DESTDIR}"/lib/cryptsetup/scripts/
fi

if [ ! -d "${DESTDIR}"/etc/ ]
then
        mkdir -p "${DESTDIR}"/etc/
fi

cp /etc/crypt.system "${DESTDIR}"/etc/

#<<EOF
"""

REFRESHGRUB = """
#!/usr/bin/env bash

####################################################################################################
#       Automated Grub refresh after kernel updates.
#
# See:
#       https://help.ubuntu.com/community/ManualFullSystemEncryption/DetailedProcessSetUpBoot
#
# This must be run with administrative permissions, i.e. with sudo.
####################################################################################################

#---------------------------------------------------------------------------------------------------
#       Copy boot modules to EFI

mkdir --parents /boot/efi/EFI/ubuntu/
(( ${?} )) && echo 'Failed to create boot modules folder in EFI.' >&2 && exit 3

cp --recursive /boot/grub/x86_64-efi /boot/efi/EFI/ubuntu/
(( ${?} )) && echo 'Failed to copy boot modules to EFI.' >&2 && exit 3

#---------------------------------------------------------------------------------------------------
#       Install and repair Grub

grub-install --target=x86_64-efi --uefi-secure-boot --efi-directory=/boot/efi --bootloader=ubuntu --boot-directory=/boot/efi/EFI/ubuntu --recheck /dev/sda
(( ${?} )) && echo 'Failed to reinstall Grub.' >&2 && exit 3

grub-mkconfig --output=/boot/efi/EFI/ubuntu/grub/grub.cfg
(( ${?} )) && echo 'Failed to reconfigure Grub.' >&2 && exit 3

#---------------------------------------------------------------------------------------------------
#       Allow Ubuntu to boot

cd /boot/efi/EFI
(( ${?} )) && echo 'Failed to enter /boot/efi/EFI.' >&2 && exit 3

[[ -d Boot ]] && rm --force --recursive Boot-backup && mv Boot Boot-backup
                                                        # Ignore error code 1.
(( ${?} > 1 )) && echo 'Failed to enter /boot/efi/EFI.' >&2 && exit 3

#---------------------------------------------------------------------------------------------------
#       Prepare initramfs

update-initramfs -ck all
(( ${?} )) && echo 'Failed to prepare initrafms.' >&2 && exit 3

#---------------------------------------------------------------------------------------------------
#       Successful end.

echo 'Successfully refreshed Grub.'
"""


def expected_partitions():
    partitions = """
BYT;
/dev/sda:256GB:scsi:512:512:gpt:ATA INTEL SSDSCKKW25:;
1:1049kB:473MB:472MB:ntfs:Basic data partition:hidden, diag;
2:473MB:577MB:104MB:fat32:EFI system partition:boot, esp;
3:577MB:593MB:16.8MB::Microsoft reserved partition:msftres;
4:593MB:172GB:171GB:ntfs:Windows system partition:msftdata;
5:172GB:256GB:84.3GB::Ubuntu system partition:;

BYT;
/dev/sdb:2000GB:scsi:512:4096:gpt:ATA ST2000DX002-2DV1:;
1:17.4kB:134MB:134MB::Microsoft reserved partition:msftres;
2:135MB:275GB:275GB::Windows data partition:msftdata;
3:275GB:2000GB:1725GB::Ubuntu data partition:;
    """.strip() + "\n"
    return partitions


def query_yes_no(question):
    prompt = " [y/n] "
    while True:
        sys.stdout.write(f"{question}{prompt}")
        choice = input().lower()
        if choice == "y":
            return True
        elif choice == "n":
            return False
        else:
            sys.stdout.write("Please respond with 'y' or 'n'\n")


def check_partitions():
    """
    Check current partitions against expectations.

    Aborts script:
        * Current partition differs from expectation
        * User elects to quit
    """
    parted_command = ["sudo", "parted", "--list", "--script"]
    p = subprocess.run(parted_command + ["--machine"],
            stdout=subprocess.PIPE,
            encoding="utf-8")

    current_partitions = "\n\n".join(p.stdout.split("\n\n")[:2]) + "\n"
    diff = list(difflib.unified_diff(
        expected_partitions().splitlines(1), 
        current_partitions.splitlines(1), 
        fromfile="expected", tofile="current", lineterm="\n"))
    if diff:
        for line in diff:
            print(f"{line}", end="")
        print()
        sys.exit("Current partition does not meet expectations! Go fix it!")
    else:
        if VERBOSE:
            subprocess.run(parted_command)
            if not query_yes_no("Do these partitions makes sense? It passed our expectations."):
                sys.exit("Go fix your partitions!")


def setup_luks():
    # make sure we have luks volumes and that they look reasonable
    is_luks = ["sudo", "cryptsetup", "isLuks"]
    for device, name in LUKS_DEVICES.items():
        p = subprocess.run(is_luks + [device])
        if p.returncode == 0:
            print(f"Device {device} is already a LUKS volume")
            if VERBOSE:
                subprocess.run(["sudo", "cryptsetup", "luksDump", device])
                if not query_yes_no("Does this look okay?"):
                    sys.exit("Go fix your luks volume!")
        else:
            raise RuntimeError(f"Did not find LUKS volume under {device}. Add some code!")


def open_luks():
    # now open them
    open_luks = ["sudo", "cryptsetup", "luksOpen"]
    is_open = ["sudo", "dmsetup", "info"]
    for device, name in LUKS_DEVICES.items():
        p = subprocess.run(is_open + [name], stdout=subprocess.PIPE, encoding='utf-8')
        if p.returncode != 0 or "ACTIVE" not in p.stdout:
            print(f"Opening {device} ({name})")
            subprocess.run(open_luks + [device, name])
        else:
            print(f"Luks device {device} opened as {name}")
            if VERBOSE:
                print(p.stdout)


def check_physical_volumes():
    for name in LUKS_DEVICES.values():
        device = f"/dev/mapper/{name}"
        p = subprocess.run(["sudo", "pvs", device])
        if p.returncode == 0:
            print(f"Physical volume {device} already exists")
        else:
            subprocess.run(["sudo", "pvcreate", device])


def check_volume_groups():
    for name, physical_volumes in VOLUME_GROUPS.items():
        p = subprocess.run(["sudo", "vgs", name])
        if p.returncode == 0:
            print(f"Volume group {name} already exists")
        else:
            for index, pv in enumerate(physical_volumes):
                if index == 0:
                    subprocess.run(["sudo", "vgcreate", name, pv])
                else:
                    subprocess.run(["sudo", "vgextend", name, pv])


def check_logical_volumes():

    lvs_exists = ["sudo", "lvs"]
    for volume_group, data in LOGICAL_VOLUMES.items():
        for volume_name, volume_data in data.items():
            # create logical volume
            logical_volume = f"/dev/mapper/{volume_group}-{volume_name}"
            p = subprocess.run(lvs_exists + [logical_volume])
            if p.returncode == 0:
                print(f"Logical Volume {logical_volume} already exists!")
            else:
                subprocess.run(["sudo", "lvcreate"] + 
                                volume_data['size'] + 
                                [f"--name={volume_name}", volume_group])
            # create filesystem
            p = subprocess.run(["sudo", "blkid", "-s", "TYPE", logical_volume],
                    stdout=subprocess.PIPE, encoding='utf-8')
            if volume_data['type'] not in p.stdout:
                command = ["sudo"] + volume_data['filesystem_command'] + [ logical_volume]
                print(" ".join(command))
                subprocess.run(command)
            else:
                print(f"Logical volume {logical_volume} already defines filesystem of type {volume_data['type']}")

def mount_partitions():
    commands = [
        "sudo mkdir /mnt/root",
        "sudo mount /dev/mapper/system-root /mnt/root",
        "sudo mount /dev/mapper/system-boot /mnt/root/boot",
        "sudo mount /dev/sda2 /mnt/root/boot/efi",
        "sudo mount /dev/mapper/data-home /mnt/root/home"
    ]
    for c in commands:
        print("DOING:", c)
        subprocess.run(c.split())
        print("DONE")

def create_key_files():
    key_files = [
            ["/mnt/root/etc/crypt.system", 'system'],
            ["/mnt/root/etc/crypt.data", 'data']
            ]
    for key, luks_name in key_files:
        if not os.path.exists(key):
            partition = LUKS_NAME_MAP[luks_name]
            p =subprocess.run(["sudo", "dd", "if=/dev/urandom", f"of={key}", "count=1", "bs=512"])
            print(f"Enter passphrase for luks device {luks_name}")
            p = subprocess.run(["sudo", "cryptsetup", "luksAddKey", partition, key])

def get_luks_uuid(partition):
    p = subprocess.run(["sudo", "cryptsetup", "luksUUID", partition],
            stdout=subprocess.PIPE, encoding='UTF-8')
    if p.returncode == 0:
        return p.stdout.strip("\n")
    raise RuntimeError(f"Something strange happened when fetching UUID for {partition}")

def move(source, dest):
    d = Path(dest).resolve().parent
    subprocess.run(["sudo", "mkdir", "-p", str(d)])
    subprocess.run(["sudo", "mv", source, dest])
    subprocess.run(["sudo", "chown", "root:root", dest])

def copy(source, dest):
    d = Path(dest).resolve().parent
    subprocess.run(["sudo", "mkdir", "-p", str(d)])
    subprocess.run(["sudo", "cp", source, dest])


def fix_crypttab():
    data = """
#<name> <source device>                           <key file>        <options>
system  UUID={system_uuid} /etc/crypt.system luks,discard,noearly,keyscript=/lib/cryptsetup/scripts/getinitramfskey.sh
data    UUID={data_uuid} /etc/crypt.data   luks,discard,noearly
    """.format(
            system_uuid=get_luks_uuid(LUKS_NAME_MAP['system']),
            data_uuid=get_luks_uuid(LUKS_NAME_MAP['data']),
        ).strip()

    with open("crypttab", "w") as f:
        f.write(data)
        f.write("\n")
    
    print("Move crypttab in place")
    move("crypttab", "/mnt/root/etc/crypttab")
    print("Set permissions")
    subprocess.run("sudo chmod -rw /mnt/root/etc/crypt*", shell=True)

def fix_grub():
    grub_final = "/mnt/root/etc/default/grub"
    subprocess.run(["sudo", "cp", grub_final, "grub"])
    
    with open("grub", "r") as f, open("grub_new", "w") as f2:
        crypto_present = False
        for line in f:
            if line.startswith("GRUB_HIDDEN_TIMEOUT=0"):
                f2.write("#GRUB_HIDDEN_TIMEOUT=0\n")
            elif line.startswith("GRUB_HIDDEN_TIMEOUT_QUIET=true"):
                f2.write("GRUB_HIDDEN_TIMEOUT_QUIET=false\n")
            elif line.startswith("GRUB_ENABLE_CRYPTODISK=y"):
                crypto_present = True
            else:
                f2.write(line)

        if not crypto_present:
            f2.write("GRUB_ENABLE_CRYPTODISK=y\n")

    move("grub_new", grub_final)
    os.remove("grub")
            
def fix_chroot_stuff():
    chroot_commands = """
***********************************
sudo mount --bind /dev /mnt/root/dev
sudo mount --bind /run /mnt/root/run
sudo chroot /mnt/root
mount --types=proc proc /proc
mount --types=sysfs sys /sys
**********************************
    """.strip()
    print(chroot_commands)
    query_yes_no("Enter the commands above in a separate terminal, then press Y")

    copy("/mnt/root/boot/efi/startup.nsh", "startup.nsh")
    if "\\EFI\\ubuntu\\grubx64.efi" not in open("startup.nsh").read():
        print(open("startup.nsh").read())
        query_yes_no("In chroot, make sure /boot/efi/startup.nsh contains '\\EFI\\ubuntu\\grubx64.efi' before continuing")
    os.remove("startup.nsh")

    with open("getinitramfskey.sh", "w") as f:
        f.write(GET_INIT_SCRIPT.strip())
    getinit_script = "/mnt/root/lib/cryptsetup/scripts/getinitramfskey.sh"
    move("getinitramfskey.sh", getinit_script)
    subprocess.run(["sudo", "chmod", "+x", getinit_script])
    
    with open("loadinitramfskey.sh", "w") as f:
        f.write(LOAD_INIT_SCRIPT.strip())
    loadinit_script = "/mnt/root/initramfs-tools/hooks/loadinitramfskey.sh"
    move("loadinitramfskey.sh", loadinit_script)
    subprocess.run(["sudo", "chmod", "+x", loadinit_script])

    with open("refreshgrub", "w") as f:
        f.write(REFRESHGRUB.strip())
    
    refresh_script = "/mnt/root/usr/local/sbin/refreshgrub"
    move("refreshgrub", refresh_script)
    subprocess.run(["sudo", "chmod", "+x", refresh_script])

    query_yes_no("In chroot, run $ refreshgrub")

if __name__=="__main__":
    parser = argparse.ArgumentParser(description="Configure Ubuntu desktop with custom, full-disk encryption")
    parser.add_argument("--safe", default=False, action='store_true', help="Print extra info")
    args = parser.parse_args()
    
    VERBOSE = args.safe

    check_partitions()
    setup_luks()
    open_luks()
    check_physical_volumes()
    check_volume_groups()
    check_logical_volumes()
    query_yes_no("Now install Ubuntu! Is it installed?")
    mount_partitions()
    create_key_files()
    fix_crypttab()
    fix_grub()
    fix_chroot_stuff()
