#!/usr/bin/env python2

import os
import sys
import argparse

def load_cfg(path):
    cfg = {}
    try:
        with open(path) as fp:
            for line in fp:
                kv = line.strip().split('#', 1)[0].split(' ', 1)
                if len(kv) != 2: continue
                cfg[kv[0]] = kv[1]
    except IOError:
        pass
    return cfg

def init(args):
    # TODO: error handling. A lot of error handling.
    import json
    from git import Repo, RootModule
    if os.path.exists(args.dir) and os.listdir(args.dir):
        sys.exit('Directory not empty.')
    repo = Repo.init(args.dir)
    os.chdir(args.dir)
    with open('.gitignore', 'w') as fp:
        fp.write('out/\n')
    os.mkdir('edges/')
    open('edges/managed.txt', 'w').close()
    open('ratings.txt', 'w').close()
    open('.gitmodules', 'w').close()
    os.mkdir('profiles')
    with open('profiles/1080p.json', 'w') as fp:
        json.dump(dict(width=1920, height=1080, duration=30, fps=24,
            skip=0, quality=3000, output=dict(format='jpeg', quality=95)),
            fp, sort_keys=True, indent=2)
    with open('profiles/preview.json', 'w') as fp:
        json.dump(dict(width=640, height=360, duration=30, fps=24,
            skip=1, quality=600, output=dict(format='jpeg', quality=90)),
            fp, sort_keys=True, indent=2)
    os.mkdir('out')
    repo.index.add(['.gitignore', 'edges', 'profiles', 'ratings.txt'])
    repo.index.commit('Initial commit')
    repo.create_submodule('cuburn', '.deps/cuburn', args.cp, 'master')
    repo.create_submodule('flockutil', '.deps/flockutil', args.fp, 'master')
    RootModule(repo).update()
    os.symlink('.deps/flockutil/flock', 'flock')
    repo.index.add(['flock'])
    repo.index.commit('Add submodules')
    repo.git.submodule('init')

def main():
    cfg = load_cfg('.flockrc')

    parser = argparse.ArgumentParser(description="Manage a flock.",
        epilog="Some options are required unless a default value is set.")

    subparsers = parser.add_subparsers()
    p = subparsers.add_parser('init', help='Create a new flock repository.')
    p.set_defaults(cmd='init')
    p.add_argument('dir', nargs='?', default='.')
    p.add_argument('-c', help='Path of remote cuburn repository.', dest='cp',
            default='git://github.com/stevenrobertson/cuburn.git')
    p.add_argument('-f', help='Path of remote flockutil repository.',
            dest='fp', default='git@bitbucket.org:srobertson/flockutil.git')

    p = subparsers.add_parser('set', help='Set default values.')
    p.set_defaults(cmd='set')
    p.add_argument('key')
    group = p.add_mutually_exclusive_group()
    group.add_argument('value', nargs='?',
            help='If omitted, current value is displayed.')
    group.add_argument('-u', dest='unset', action='store_true',
            help='Unset the given key. No value permitted.')

    p = subparsers.add_parser('convert',
            help='Convert XML nodes to JSON edges.')
    p.set_defaults(cmd='convert')
    p.add_argument('nodes', metavar='FILE', nargs='+', type=file,
            help='XML genomes.')
    p.add_argument('-o', dest='output', metavar='DIR', default='edges',
            help='Specify alternate output directory.')
    p.add_argument('-f', dest='force', action='store_true',
            help='Force operation to proceed.')

    p = subparsers.add_parser('render', help='Render a flock.')
    p.set_defaults(cmd='render')
    p.add_argument('-p', dest='profile', default=cfg.get('profile'),
            help='Specify a profile. (Key: "profile")')
    p.add_argument('edges', metavar='edge', nargs='*',
            help='Edge or loop names to render.')

    args = parser.parse_args()
    if args.cmd == 'render' and args.profile is None:
        parser.error('"-p" is required when no default profile is set.')

    if args.cmd == 'init':
        return init(args)

    import flock
    flock.Flockutil(args)

if __name__ == "__main__":
    main()
