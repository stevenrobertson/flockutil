#!/usr/bin/env python2

import os
import sys
import json
import warnings
from os.path import isfile, join
from hashlib import sha1
import numpy as np
from itertools import ifilter
from git import *

from cuburn.genome import Genome, Palette
from cuburn.render import Renderer

FLOCK_PATH_SET = bool(os.environ.get('FLOCK_PATH'))
if FLOCK_PATH_SET:
    warnings.warn('Flock path is set, disabling dependency tracking.')

# Commands, most unimplemented:
#   init        - Starts a Git repository with the appropriate structure
#   set         - Set default values to use in commands
#   import      - Reads an XML file, writes JSON
#   export      - Reads or creates JSON, converts to XML
#   render      - Renders output
#   show        - Displays latest rendering results to user
#   review      - Displays unreviewed edges, solicits reviews
#   createEdge  - Make a single edge file from genomes
#   addEdges    - Selects edges to add to the auto list
#   serve       - Launches a distribution server for multi-card rendering
#   host        - Connect to a server and perform its renders
#   encode      - Go from one format to another
#
# init will be implemented separately so users don't have to clone twice

class Flockutil(object):
    def __init__(self, args):
        self.args = args
        self.repo = Repo('.')
        getattr(self, 'cmd_' + args.cmd)(args)

    def load_edge(self, edge):
        paths = set(['.deps'])
        def read(path):
            paths.add(path)
            return open(path)
        prof = json.load(read(join('profiles', self.args.profile + '.json')))
        gnm = Genome(json.load(read('edges/%s.json' % edge)), prof['quality'])
        gnm.color.palette = [(t, Palette(read('palettes/%s.rgb8' % p).read()))
                             for t, p in gnm.color.palette]
        err, times = gnm.set_timing(prof['duration'], prof['fps'])
        rev = next(self.repo.iter_commits(paths=list(paths))).hexsha[:12]
        # TODO: also track subrepos
        if FLOCK_PATH_SET or paths.intersection(self.repo.untracked_files):
            rev = 'untracked'
        return prof, rev, gnm, err, times

    def cmd_render(self, args):
        import scipy
        import pycuda.autoinit

        if args.edges:
            edges = args.edges
        else:
            if self.repo.is_dirty():
                sys.exit('Index or working copy has uncommitted changes.\n'
                         'Commit them or specify specific edges to render.')
            # TODO: automatically generated edges
            # TODO: sort by rating, when available
            # TODO: perform a random walk a la the sheep player, so that
            #       contiguous sequences are rendered first when possible
            # TODO: playlist mode
            edges = [blob.path[6:-5].replace('/', '_')
                     for blob in self.repo.head.commit.tree['edges'].traverse()
                     if blob.path.endswith('.json')]

        for edge in edges:
            prof, rev, gnm, err, times = self.load_edge(edge)
            odir = join('out', args.profile, edge, rev)
            if not os.path.isdir(odir):
                os.makedirs(odir)
            llink = join('out', args.profile, edge, 'latest')
            if os.path.islink(llink):
                os.unlink(llink)
            os.symlink(rev, llink)

            render = Renderer()
            rt = [(os.path.join(odir, '%05d.jpg' % (i+1)), tc)
                  for i, tc in enumerate(times)][::prof['skip']+1]
            if rev != 'untracked':
                rt = filter(lambda r: not os.path.isfile(r[0]), rt)
            for out in render.render(gnm, rt, prof['width'], prof['height']):
                noalpha = out.buf[:,:,:3]
                img = scipy.misc.toimage(noalpha, cmin=0, cmax=1)
                img.save(out.idx, quality=95)
                print out.idx

    def cmd_set(self, args):
        from main import load_cfg
        cfg = load_cfg('.flockrc')
        if args.value:
            cfg[args.key] = args.value
        elif args.unset:
            del cfg[args.key]
        else:
            print args.key, cfg[args.key] if args.key in cfg else '[unset]'
            return
        with open('.flockrc', 'w') as fp:
            fp.write('\n'.join(map(' '.join, cfg.items())))

