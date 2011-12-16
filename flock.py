#!/usr/bin/env python2

import os
import sys
import json
import argparse
import warnings
from os.path import isfile, join
from hashlib import sha1
import numpy as np
from itertools import ifilter
from git import *

from cuburn.genome import Genome, Palette
from cuburn.render import Renderer
import scipy

FLOCK_PATH_SET = bool(os.environ.get('FLOCK_PATH'))
if FLOCK_PATH_SET:
    warnings.warn('Flock path is set, disabling dependency tracking.')

# Commands, most unimplemented:
#   init        - Starts a Git repository with the appropriate structure
#   import      - Reads an XML file, writes JSON
#   export      - Reads or creates JSON, converts to XML
#   render      - Renders output
#   addEdges    - Selects edges to add to this flock's index
#   review      - Plays individual edges; records weights

class Flockutil(object):
    def __init__(self, args):
        self.args = args
        self.repo = Repo('.')
        getattr(self, 'cmd_' + args.cmd)()
        self.fmt, self.fmt_path = None, None

    def cmd_init(self):
        pass

    def load_fmt(self):
        fmtpath = join('formats', self.args.format + '.json')
        with open(fmtpath) as fp:
            return json.load(fp), fmtpath

    def load_edge(self, edge, fmt, fmtpath):
        paths = [fmtpath]
        def read(path):
            paths.append(path)
            return open(path)
        gnm = Genome(json.load(read('edges/%s.json' % edge)), fmt['quality'])
        gnm.color.palette = [(t, Palette(read('palettes/%s.rgb8' % p).read()))
                             for t, p in gnm.color.palette]
        err, times = gnm.set_timing(fmt['duration'], fmt['fps'])
        rev = next(self.repo.iter_commits(paths=paths)).hexsha[:10]
        # TODO: also track subrepos
        if FLOCK_PATH_SET or repo.is_dirty():
            rev = 'untracked'
        return rev, gnm, err, times

    def cmd_render(self):
        if not self.args.edges:
            print 'Auto-render not implemented yet'
            return

        import pycuda.autoinit
        fmt, fmtpath = self.load_fmt()
        for edge in self.args.edges:
            rev, gnm, err, times = self.load_edge(edge, fmt, fmtpath)
            odir = join('out', self.args.format, edge, rev)
            if not os.path.isdir(odir):
                os.makedirs(odir)
            llink = join('out', self.args.format, edge, 'latest')
            if os.path.islink(llink):
                os.unlink(llink)
            os.symlink(rev, llink)

            render = Renderer()
            rt = [(os.path.join(odir, '%05d.jpg' % (i+1)), tc)
                  for i, tc in enumerate(times)][::fmt['skip']+1]
            if rev != 'untracked':
                rt = filter(lambda r: not os.path.isfile(r[0]), rt)
            for out in render.render(gnm, rt, fmt['width'], fmt['height']):
                noalpha = out.buf[:,:,:3]
                img = scipy.misc.toimage(noalpha, cmin=0, cmax=1)
                img.save(out.idx, quality=95)

def main():
    parser = argparse.ArgumentParser(description="Manage a flock.")

    subparsers = parser.add_subparsers()
    p = subparsers.add_parser('init', help='Create a flock repo.')
    p.set_defaults(cmd='init')
    p.add_argument('dir', nargs='?', default='.')

    p = subparsers.add_parser('render', help='Render a flock.')
    p.set_defaults(cmd='render')
    p.add_argument('format', help='The format profile to use.')
    p.add_argument('edges', help='Edge or loop names to render.', nargs='+')

    args = parser.parse_args()
    Flockutil(args)

if __name__ == "__main__":
    main()

