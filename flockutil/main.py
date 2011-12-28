#!/usr/bin/env python2

import os
import sys
import argparse
from subprocess import check_call

def parse_simple(path):
    """Parse a simple line-based file, stripping comments and empty lines."""
    try:
        with open(path) as fp:
            for line in fp:
                line = line.strip().split('#', 1)[0]
                if not line: continue
                yield line
    except:
        import traceback
        traceback.print_exc()
        sys.exit('Problem parsing ' + path)

def load_cfg(path):
    cfg = {}
    for line in parse_simple(path):
        kv = line.split(' ', 1)
        if len(kv) != 2: continue
        cfg[kv[0]] = kv[1]
    return cfg

INITIAL_REPO = {
    '.gitignore': 'out/',
    'edges/managed.txt': "# Add edges here or use './flock addEdges'.",
    'ratings.txt': '# edgename revid user flags [comments]',
    'profiles/1080p.json': dict(width=1920, height=1080, duration=30, fps=24,
            skip=0, quality=3000, output=dict(format='jpeg', quality=95)),
    'profiles/preview.json': dict(width=640, height=360, duration=30, fps=24,
            skip=1, quality=600, output=dict(format='jpeg', quality=90))
}

def init(args):
    # TODO: error handling. A lot of error handling.
    import json
    if os.path.exists(args.dir) and os.listdir(args.dir):
        sys.exit('Directory not empty.')
    check_call(['git', 'init', args.dir])
    os.chdir(args.dir)
    check_call(['git', 'submodule', 'add', args.cp, '.deps/cuburn'])
    check_call(['git', 'submodule', 'add', args.fp, '.deps/flockutil'])
    for dir in 'edges profiles out'.split():
        os.mkdir(dir)
    for path, contents in INITIAL_REPO.items():
        if isinstance(contents, dict):
            contents = json.dumps(contents, sort_keys=True, indent=2)
        contents = contents.strip() + '\n'
        with open(path, 'w') as fp:
            fp.write(contents)
    os.symlink('.deps/flockutil/flock', 'flock')
    check_call(['git', 'add', '-A'])
    check_call(['git', 'commit', '-m', 'Initial commit.'])

def mkparser():
    cfg = load_cfg('.flockrc') if os.path.isfile('.flockrc') else {}

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
    p.add_argument('edges', metavar='edge', nargs='*',
            help='Edge or loop names to render.')
    p.add_argument('-p', dest='profile', default=cfg.get('profile'),
            help='Specify a profile. (Key: "profile")')
    p.add_argument('-m', dest='match', action='store_true',
            help='Match any edge whose name contains the given substring, '
            'instead of matching names exactly.')
    # TODO: this should be replaced by graph-aware ordering
    p.add_argument('-c', dest='committed', action='store_true',
            help='Render committed edges before managed ones.')
    p.add_argument('-r', dest='randomize', action='store_true',
            help='Render edges and frames in random order. (Useful when '
            'running multiple instances simultaneously.)')
    p.add_argument('-t', dest='thresh', default=2, type=int,
            help='Only render edges with at least this rating (2). (Unrated '
            'edges have a default rating of 2.5.)')
    p.add_argument('--passes', default=1, type=int,
            help='Skip 2^(passes-1) frames at first, come back for them later')
    p.add_argument('--ignore-ratings', action='store_true',
            help="Don't use ratings to sort render order.")

    p = subparsers.add_parser('blend',
            help='Create an edge that blends between two others.')
    p.set_defaults(cmd='blend')
    p.add_argument('left', help='Name (or file) of genome to start at')
    p.add_argument('right', help='Name (or file) of genome to end at')
    p.add_argument('-a', dest='align', default='weightflip',
            choices='natural weight weightflip color'.split(),
            help='Sort method used to align xforms')
    p.add_argument('-b', dest='blur', metavar='STDEV', type=float, const=1.5,
            nargs='?', help='Blur palettes during interpolation (1.5)')
    p.add_argument('-l', dest='nloops', metavar='LOOPS', type=int, default=2,
            help='Number of loops to use (also scales duration) (2)')
    p.add_argument('-s', dest='stagger', action='store_true',
            help='Use stagger (experimental!)')
    p.add_argument('-o', dest='out', help='Output filename')

    p = subparsers.add_parser('update',
            help="Link output directories which don't need re-rendering.",
            epilog="""
This command scans the output directories for each output profile. For each
profile and edge which both requires re-rendering and has enough frames in the
'latest' directory, a small number of frames will be rendered. If those frames
match, the directory for the current revid will be symlinked to the directory
at which 'latest' points; otherwise, a new directory will be created with the
test frames in it.

Frames can match based on absolute image metric similarity. If that match
fails, as it often will for noisy images, the frames are re-rendered, and the
relative difference between the two frames rendered using current versions is
used as an alternative threshold.
""")
    p.set_defaults(cmd='update')
    p.add_argument('-d', dest='deps', action='store_false',
            help='Skip checking for updated dependencies')
    p.add_argument('-r', dest='renders', action='store_false',
            help='Skip checking old frames')
    p.add_argument('-n', dest='nframes', type=int, default=4,
            help='Number of frames to test (4)')
    p.add_argument('-p', dest='profiles', action='append',
            help='Profile to test (all), may be given multiple times')
    p.add_argument('--diff', type=float, default=0.01,
            help='Maximum mean SSIM deviation to accept frame (0.01)')
    p.add_argument('--reldiff', type=float, default=1.1,
            help='Maximum relative SSIM error to accept frame (1.1)')
    return parser

def main():
    parser = mkparser()
    args = parser.parse_args()
    if args.cmd == 'render' and args.profile is None:
        parser.error('"-p" is required when no default profile is set.')

    if args.cmd == 'init':
        return init(args)

    import flock
    flock.Flockutil(args)

if __name__ == "__main__":
    main()
