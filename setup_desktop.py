#!/usr/bin/python3
"""
Configure Ubuntu Box ... just how we like it ;)

TODO:
    * fancy git command-line
    * terminator
    * nord themes for tmux and terminator
    * sshd configuration
    * setup dock
    * symlink to configuration files
    * git global config
"""

import argparse
from pathlib import Path
import subprocess
import urllib.request

DEV_PKGS = [
    "ack",
    "git",
    "gitk",
    "tmux",
    "tree",
    "vim",
]

UTIL_PKGS = [
        "gnome-tweak-tool",
        "xclip",
        ]

APP_PKGS = [
    "nautilus-dropbox",
    # instead of python-gpgme?
    "python-gpg",
]

PACKAGES = DEV_PKGS + UTIL_PKGS + APP_PKGS

PPAS = [
    # Nvidia drivers
    "ppa:graphics-drivers/ppa"
]

UI_TWEAKS = [
    ["org.gnome.settings-daemon.plugins.color", "night-light-enabled", "true"],
    ["org.gnome.desktop.interface", "gtk-theme", '"Adwaita-dark"'],
    ["org.gnome.desktop.interface", "cursor-theme", '"Whiteglass"'],
    ["org.gnome.desktop.interface", "icon-theme", '"Humanity"'],

    ]

def update_drivers():
    """ Install any needed drivers (does nvidia, maybe others?) """
    print("Update drivers")
    subprocess.run(["sudo", "ubuntu-drivers", "autoinstall"], check=True)

def add_ppas():
    """ Add our PPAs, and only update cache at the end """
    print("Adding PPAs")
    for ppa in PPAS:
        subprocess.run(
                ["sudo", "add-apt-repository", "-y", "--no-update", ppa],
                check=True)
    subprocess.run(["sudo", "apt", "update"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def install_packages(packages=PACKAGES):
    print("Installing Packages: ", packages)
    subprocess.run(["sudo", "apt", "install", "-y"] + packages, check=True)

def is_installed(package_name):
    p = subprocess.run(["dpkg", "-l", package_name], stdout=subprocess.DEVNULL, timeout=0.05)
    return p.returncode == 0

def install_nautilus():
    print("Ensure Dropbox installed")
    if not is_installed("nautilus-dropbox"):
        install_packages(packages=["nautilus-dropbox"])
        # trigger dropbox setup
        subprocess.run(["nautilus", "--quit"])

def change_display_settings():
    print("Updating display settings")
    for tweak in UI_TWEAKS:
        subprocess.run(["gsettings", "set"] + tweak, check=True)

def install_chrome():
    """ Because 1password does not yet support Firefox"""
    print("Ensure google chrome and 1Password installed")
    if not is_installed("google-chrome-stable"):
        deb_file = "/tmp/chrome.deb"
        urllib.request.urlretrieve("https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb", deb_file)
        install_packages([deb_file])
        subprocess.run(["google-chrome", "https://chrome.google.com/webstore/detail/1password-extension-deskt/aomjjhallfgjeglblehebfpbcfeobpgk?hl=en"])

def set_dock_apps():
    """ TODO: Just file folder, terminal, google chrome"""
    pass

def setup_github_keys():
    filename = "github_id_rsa"
    key_path = Path.home().joinpath(".ssh", filename)
    if not key_path.exists():
        subprocess.run(
            f"ssh-keygen -t rsa -b 4096 -C desktop-github -f $HOME/.ssh/{filename}",
            shell=True)
        print(f"Add {key_path} public key to Github account")
    else:
        subprocess.run(f"xclip -sel clip < $HOME/.ssh/{filename}.pub", shell=True)


if __name__=="__main__":
    add_ppas()
    update_drivers()
    install_nautilus()
    install_chrome()
    install_packages()
    change_display_settings()
    setup_github_keys()
