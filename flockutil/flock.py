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

from cuburn import genome, render
import blend

FLOCK_PATH_IGNORE = bool(os.environ.get('FLOCK_PATH_IGNORE'))
FLOCK_PATH_SET = bool(os.environ.get('FLOCK_PATH')) and not FLOCK_PATH_IGNORE
if FLOCK_PATH_IGNORE:
    warnings.warn('DEVS ONLY! Ignoring submodule revisions entirely.')
elif FLOCK_PATH_SET:
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
        self.repo = Repo('.')
        try:
            self.managed_edges = dict(self.parse_managed())
        except:
            import traceback
            traceback.print_exc()
            sys.exit('Problem parsing edges/managed.txt')
        getattr(self, 'cmd_' + args.cmd)(args)

    def parse_managed(self):
        # TODO: parse ratings, exclude bad managed edges
        for line in open('edges/managed.txt'):
            line = line.strip().split('#', 1)[0]
            if not line: continue
            idx = sha1(line).hexdigest()[:5]
            args = line.split()
            l, r = args[:2]
            yield '%s.%s=%s' % (idx, l, r), args

    def cmd_convert(self, args):
        did = 0
        for node in args.nodes:
            flames = genome.XMLGenomeParser.parse(node.read())
            if len(flames) > 10 and not args.force:
                print ('In file %s:\n'
                    'This looks like an XML frame-by-frame animation.\n'
                    'Try importing just the keyframes, or use "-f" to force.'
                    % node.name)
                continue
            basename = os.path.basename(node.name).rsplit('.', 1)[0]
            names = ['%s_%d' % (basename, i) for i in range(len(flames))]
            if len(flames) == 1:
                names = [basename]
            for name, flame in zip(names, flames):
                path = os.path.join('edges', name + '.json')
                if os.path.isfile(path) and not args.force:
                    print 'Not overwriting %s (use "-f" to force).' % path
                    continue
                out = genome.json_encode_genome(genome.convert_flame(flame))
                with open(path, 'w') as fp:
                    fp.write(out.lstrip())
                did += 1
        if did > 0:
            print ("Wrote %d genomes. Remember to run 'git add' and "
                   "'git commit'." % did)

    def get_rev(self, paths):
        if not FLOCK_PATH_IGNORE:
            paths = paths + ['.deps']
        if FLOCK_PATH_SET or set(paths).intersection(self.repo.untracked_files):
            return 'untracked'
        for p in paths:
            # The above doesn't catch files outside of the tree entirely
            try:
                self.repo.head.commit.tree / p
            except IndexError:
                return 'untracked'
        return next(self.repo.iter_commits(paths=paths)).hexsha[:12]

    def load_managed_edge(self, edge):
        matches = filter(lambda e: e.startswith(edge), self.managed_edges)
        if len(matches) == 0:
            raise IndexError('No matches for edge named "%s"' % edge)
        elif len(matches) > 1:
            raise IndexError('"%s" ambiguous (could match %s)'
                             % (edge, ', '.join(matches)))
        name = matches[0]
        args = self.managed_edges[name]
        # It's a requirement that managed edges be present among edges/*.json
        # TODO: handle this earlier? fail gracefully when files are missing?
        paths = ['edges/%s.json' % s for s in args[:2]]
        rev = self.get_rev(paths)
        path = 'out/cache/%s.%s.json' % (name, rev)
        if not os.path.isfile(path):
            from main import mkparser
            parser = mkparser()
            args = parser.parse_args(['blend'] + args)
            gnm, paths = self.blend(args)
            if not os.path.isdir('out/cache'):
                os.makedirs('out/cache')
            with open(path, 'w') as fp:
                fp.write(gnm)
        return set(paths), genome.Genome(json.load(open(path)))

    def load_edge(self, edge):
        # TODO: check for changes in linked edges and warn/error
        # TODO: support abbreviations
        # TODO: detect edges specified by filename
        if not os.path.isfile(edge):
            p = join('edges', edge + '.json')
            if not os.path.isfile(p):
                return self.load_managed_edge(edge)
            edge = p
        return set([edge]), genome.Genome(json.load(open(edge)))

    def cmd_render(self, args):
        import scipy
        import pycuda.autoinit

        if args.edges:
            edges = args.edges
        else:
            if self.repo.is_dirty():
                sys.exit('Index or working copy has uncommitted changes.\n'
                         'Commit them or specify specific edges to render.')
            # TODO: sort by rating, when available
            # TODO: perform a random walk a la the sheep player, so that
            #       contiguous sequences are rendered first when possible
            # TODO: playlist mode
            edges = [blob.path[6:-5].replace('/', '_')
                     for blob in self.repo.head.commit.tree['edges'].traverse()
                     if blob.path.endswith('.json')]
            edges += self.managed_edges.keys()
        if args.randomize:
            np.random.shuffle(edges)

        ppath = join('profiles', args.profile + '.json')
        prof = json.load(open(ppath))

        for edge in edges:
            paths, gnm = self.load_edge(edge)
            rev = self.get_rev(list(paths) + [ppath])
            err, times = gnm.set_profile(prof)
            odir = join('out', args.profile, edge, rev)
            if not os.path.isdir(odir):
                os.makedirs(odir)
            llink = join('out', args.profile, edge, 'latest')
            if os.path.islink(llink):
                os.unlink(llink)
            os.symlink(rev, llink)

            renderer = render.Renderer()
            rt = [(os.path.join(odir, '%05d.jpg' % (i+1)), tc)
                  for i, tc in enumerate(times)][::prof['skip']+1]
            if args.randomize:
                np.random.shuffle(rt)
            if rev != 'untracked':
                rt = ifilter(lambda r: not os.path.isfile(r[0]), rt)
            for out in renderer.render(gnm, rt, prof['width'], prof['height']):
                noalpha = out.buf[:,:,:3]
                img = scipy.misc.toimage(noalpha, cmin=0, cmax=1)
                img.save(out.idx, quality=95)
                print out.idx

    def blend(self, args):
        # TODO: check for canonicality of edges
        paths, left = self.load_edge(args.left)
        paths_, right = self.load_edge(args.right)
        paths.update(paths_)

        lxf, rxf = blend.align_xforms(left, right, args.align)
        left['xforms'], right['xforms'] = lxf, rxf
        bl = blend.blend_dicts(left, right, args.nloops)
        if args.blur:
            blend.blur_palettes(bl, args.blur)
        return genome.json_encode_genome(bl), paths

    def cmd_blend(self, args):
        gnm, paths = self.blend(args)
        # TODO: this breaks when using paths to specify edges
        out = args.out or ('%s=%s.json' % (args.left, args.right))
        with open(out, 'w') as fp:
            fp.write(gnm)
        print 'Wrote %s.' % out

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
