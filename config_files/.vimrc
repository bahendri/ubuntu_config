" General configuration
set nu
set tabstop=4
set shiftwidth=4
set expandtab
set colorcolumn=72,100
set eol

" Python style guide configuration
autocmd FileType python setlocal expandtab tabstop=4 shiftwidth=4
autocmd BufWritePre *.py :%s/\s\+$//e
:highlight ExtraWhitespace ctermbg=red guibg=red
:match ExtraWhitespace /\s\+$/
autocmd BufWinEnter *.py match ExtraWhitespace /\s\+$/
autocmd InsertEnter *.py match ExtraWhitespace /\s\+\%#\@<!$/
autocmd InsertLeave *.py match ExtraWhitespace /\s\+$/
autocmd BufWinLeave *.py call clearmatches()

" install vim-plug
if empty(glob('~/.vim/autoload/plug.vim'))
  silent !curl -fLo ~/.vim/autoload/plug.vim --create-dirs
    \ https://raw.githubusercontent.com/junegunn/vim-plug/master/plug.vim
  autocmd VimEnter * PlugInstall --sync | source $MYVIMRC
endif

" vim-plug
call plug#begin('~/.vim/plugged')
Plug 'scrooloose/nerdcommenter'
call plug#end()
