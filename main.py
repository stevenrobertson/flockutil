#!/usr/bin/env python2

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

def main():
    cfg = load_cfg('.flockrc')

    parser = argparse.ArgumentParser(description="Manage a flock.",
        epilog="Some options are required unless a default value is set.")

    subparsers = parser.add_subparsers()
    p = subparsers.add_parser('set', help='Set default values.')
    p.set_defaults(cmd='set')
    p.add_argument('key')
    group = p.add_mutually_exclusive_group()
    group.add_argument('value', nargs='?',
            help='If omitted, current value is displayed.')
    group.add_argument('-u', dest='unset', action='store_true',
            help='Unset the given key. No value permitted.')

    p = subparsers.add_parser('render', help='Render a flock.')
    p.set_defaults(cmd='render')
    p.add_argument('-p', dest='profile', default=cfg.get('profile'),
            required='profile' not in cfg,
            help='Specify a profile. (Key: "profile")')
    p.add_argument('edges', metavar='edge', nargs='+',
            help='Edge or loop names to render.')

    args = parser.parse_args()

    import flock
    flock.Flockutil(args)

if __name__ == "__main__":
    main()
