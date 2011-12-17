#!/usr/bin/env python2

import os
import sys
import json
import warnings
from os.path import isfile, join
import numpy as np
from itertools import ifilter
from git import *

from cuburn.genome import Genome, Palette, json_encode_genome
from cuburn.render import Renderer

import convert_xml

FLOCK_PATH_SET = bool(os.environ.get('FLOCK_PATH'))
if FLOCK_PATH_SET:
    warnings.warn('Flock path is set, disabling dependency tracking.')

# Commands, most unimplemented:
#   init        - Starts a Git repository with the appropriate structure
#   set         - Set default values to use in commands
#   convert     - Reads an XML file, writes JSON
#   export      - Reads or creates JSON, converts to XML
#   render      - Renders output
#   show        - Displays latest rendering results to user
#   review      - Displays unreviewed edges, solicits reviews
#   createEdge  - Make a single edge file from genomes
#   addEdges    - Selects edges to add to the auto list
#   serve       - Launches a distribution server for multi-card rendering
#   host        - Connect to a server and perform its renders
#   encode      - Go from one format to another
#   editPalette - Writes a temporary image file, lets the user edit, then
#                 re-embeds the palette into the source genome
#
# init is implemented separately so users don't have to clone twice

class Flockutil(object):
    def __init__(self, args):
        self.args = args
        self.repo = Repo('.')
        getattr(self, 'cmd_' + args.cmd)(args)

    def cmd_convert(self, args):
        did = 0
        for node in self.args.nodes:
            p = convert_xml.GenomeParser()
            p.parse(node)
            if len(p.flames) > 10 and not args.force:
                print ('In file %s:\n'
                    'This looks like an XML frame-by-frame animation.\n'
                    'Try importing just the keyframes, or use "-f" to force.'
                    % node.name)
                continue
            basename = os.path.basename(node.name).rsplit('.', 1)[0]
            names = ['%s_%d' % (basename, i) for i in range(len(p.flames))]
            if len(p.flames) == 1:
                names = [basename]
            for name, flame in zip(names, p.flames):
                path = os.path.join('edges', name + '.json')
                if os.path.isfile(path) and not args.force:
                    print 'Not overwriting %s (use "-f" to force).' % path
                    continue
                out = json_encode_genome(convert_xml.convert_flame(flame))
                with open(path, 'w') as fp:
                    fp.write(out.lstrip())
                did += 1
        if did > 0:
            print ("Wrote %d genomes. Remember to run 'git add' and "
                   "'git commit'." % did)

    def load_edge(self, edge):
        ppath = join('profiles', self.args.profile + '.json')
        gpath = join('edges', edge + '.json')
        paths = [ppath, gpath, '.deps']
        prof = json.load(open(ppath))
        gnm = Genome(json.load(open(gpath)))
        err, times = gnm.set_profile(prof)
        rev = next(self.repo.iter_commits(paths=paths)).hexsha[:12]
        if FLOCK_PATH_SET or set(paths).intersection(self.repo.untracked_files):
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
        if args.randomize:
            np.random.shuffle(edges)

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
            if args.randomize:
                np.random.shuffle(rt)
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

