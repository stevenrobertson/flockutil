#!/usr/bin/env python2

import os
import sys
import json
import warnings
from os.path import isfile, join
from hashlib import sha1
from subprocess import check_output
import numpy as np
from itertools import ifilter

from cuburn import genome, render

from main import parse_simple
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

# The key representing untracked files (mostly for readability)
UNTR = (-1, 'untracked')

class Flock(object):
    """
    The collection of flames which comprise the current flock.
    """
    def __init__(self):
        self.paths, self.revmap = self.parse_log()
        self.dirty = self.parse_status()
        self.managed = dict(self.parse_managed())
        self.ratings = self.parse_ratings()

        for d in self.dirty.intersection(self.paths):
            self.paths[d] = UNTR

        if not FLOCK_PATH_IGNORE:
            deprev = min(self.paths['.deps/cuburn'],
                         self.paths['.deps/flockutil'])
            if FLOCK_PATH_SET:
                deprev = UNTR
            for k, v in self.paths.items():
                self.paths[k] = min(deprev, v)

        self.edges = dict((k[6:-5].replace('/', '_'), v)
                          for k, v in self.paths.items()
                          if k.startswith('edges/') and k.endswith('.json'))

    @staticmethod
    def parse_status():
        out = check_output(['git', 'status', '-z', '-uno'])
        return set(filter(None, [f for l in out for f in l[3:].split('\0')]))

    @staticmethod
    def parse_log():
        """
        Parses the revision history for the current branch to determine the
        latest revision at which a given file was changed.
        """
        revlist = []
        paths = {}
        rev = None
        log = check_output(['git', 'log', '--name-only', '--pretty=format:%H'])
        for line in log.split('\n'):
            if rev is None:
                rev = line
                revlist.append(line)
            elif line == '':
                rev = None
            else:
                paths.setdefault(line, (len(revlist), rev))

        # Identify the smallest unique prefix to use as the revid (min 6). If
        # there is a collision, the newer revid will be extended, but the
        # older revid will not change length.
        shortrefs = set()
        revmap = {}
        revrevmap = {}
        for i in reversed(revlist):
            for j in range(6, 41):
                short = i[:j]
                if short not in shortrefs:
                    shortrefs.add(short)
                    revmap[short] = i
                    revrevmap[i] = short
                    break

        # Update the paths to use shorter revs.
        for k, v in paths.items():
            paths[k] = (v[0], revrevmap[v[1]])

        return paths, revmap

    @staticmethod
    def parse_managed():
        # TODO: parse ratings, exclude bad managed edges
        for line in parse_simple('edges/managed.txt'):
            idx = sha1(line).hexdigest()[:5]
            args = line.split()
            l, r = args[:2]
            yield '%s=%s.%s' % (l, r, idx), args

    @staticmethod
    def parse_ratings():
        ratings = {}
        for line in parse_simple('ratings.txt'):
            sp = line.split()
            if len(sp) < 4: continue
            name, rev, user, flags = sp[:4]
            if flags[0] not in '012345': continue
            ratings.setdefault(name, {}).setdefault(rev, {}).setdefault(user,
                    (int(flags[0]), flags[1:]))
        return ratings

    def find_edge(self, edge):
        """
        Given an edge name, return (name, path, rev, managed). If 'managed' is
        True, 'path' will consist of the argument list used to create the
        edge, rather than the filesystem path to the genome file.
        """
        path = 'edges/%s.json' % edge
        if path in self.paths:
            return (edge, path, self.paths[path][1], False)
        elif edge in self.managed:
            rev = min([self.edges.get(k, UNTR)
                       for k in self.managed[edge][:2]])
            return (edge, self.managed[edge], rev[1], True)
        elif os.path.isfile(edge):
            name = os.path.basename(path).split('.', 1)[0]
            rev = self.paths.get(edge, UNTR)
            return (name, edge, rev[1], False)
        else:
            raise KeyError('Could not find edge "%s".' % edge)

    def match_edges(self, match):
        matches = filter(lambda e: match in e,
                         self.edges.keys() + self.managed.keys())
        if matches:
            return matches
        if os.path.isfile(match):
            return [match]
        raise KeyError('No edges matched "%s".' % match)

    def get_rating(self, edge, default=2.5):
        if edge not in self.ratings:
            return default
        rev = self.find_edge(edge)[2]
        if rev in self.ratings[edge]:
            ratings = [v[0] for v in self.ratings[edge][rev].values()]
        else:
            ratings = [v[0] for d in self.ratings[edge].values()
                            for v in d.values()]
        return sum(ratings) / float(len(ratings))

    def list_flock(self, shuffle=False, rating=False, separate=False,
                   thresh=0):
        """
        Return a list of edges in the flock. If 'shuffle' is True, the order
        will be randomized. If 'rating' is True, the edges will be sorted by
        rating. If 'separate' is True, edges committed to the repository will
        be listed before managed edges.

        If multiple of these are True, they are applied in the order given in
        the method signature using stable sorting.

        Edges with rating lower than 'thresh' will be omitted.
        """
        edges = [e for e in self.edges.keys() + self.managed.keys()
                 if self.get_rating(e) >= thresh]
        if shuffle:
            np.random.shuffle(edges)
        else:
            edges.sort()
        if rating:
            edges.sort(key=lambda e: -self.get_rating(e))
        if separate:
            edges.sort(key=lambda e: e not in self.edges)
        return edges

class Flockutil(object):
    def __init__(self, args):
        self.flock = Flock()
        getattr(self, 'cmd_' + args.cmd)(args)

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

    def cache_managed_edge(self, name, args, rev):
        path = 'out/cache/%s.%s.json' % (name, rev)
        if not os.path.isfile(path):
            from main import mkparser
            parser = mkparser()
            args = parser.parse_args(['blend'] + args)
            bname, paths, gnm = self.blend(args)
            if not os.path.isdir('out/cache'):
                os.makedirs('out/cache')
            with open(path, 'w') as fp:
                fp.write(gnm)
        return path

    def load_edge(self, edge):
        # TODO: check for changes in linked edges and warn/error
        name, path, rev, managed = self.flock.find_edge(edge)
        if managed:
            path = self.cache_managed_edge(name, path, rev)
        with open(path) as fp:
            return genome.Genome(json.load(fp)), name, rev

    def cmd_render(self, args):
        import scipy
        import pycuda.autoinit

        if args.edges:
            if args.match:
                edges = set(sum(map(self.flock.match_edges, args.edges), []))
            else:
                edges = args.edges
        else:
            if self.flock.dirty:
                sys.exit('Index or working copy has uncommitted changes.\n'
                         'Commit them or specify specific edges to render.')
            # TODO: playlist mode
            edges = self.flock.list_flock(args.randomize,
                    not args.ignore_ratings, args.committed, args.thresh)

        ppath = join('profiles', args.profile + '.json')
        prof = json.load(open(ppath))

        for p in range(args.passes):
            for edge in edges:
                print 'Rendering %s' % edge
                gnm, name, rev = self.load_edge(edge)
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
                      for i, tc in enumerate(times)]
                rt = rt[::(prof['skip']+1)*(2**(args.passes-p-1))]
                if args.randomize:
                    np.random.shuffle(rt)
                if rev != 'untracked':
                    rt = ifilter(lambda r: not os.path.isfile(r[0]), rt)
                w, h = prof['width'], prof['height']
                for out in renderer.render(gnm, rt, w, h):
                    noalpha = out.buf[:,:,:3]
                    img = scipy.misc.toimage(noalpha, cmin=0, cmax=1)
                    img.save(out.idx, quality=95)
                    print 'Wrote %s (took %5d ms)' % (out.idx, out.gpu_time)

    def blend(self, args):
        # TODO: check for canonicity of edges
        lname, lpath, lrev, m = self.flock.find_edge(args.left)
        rname, rpath, rrev, m = self.flock.find_edge(args.right)
        name = '%s=%s' % (lname, rname)
        l, r = [genome.Genome(json.load(open(p))) for p in (lpath, rpath)]
        bl = blend.blend_genomes(l, r, nloops=args.nloops, align=args.align,
                stagger=args.stagger, blur=args.blur)
        return name, min(lrev, rrev)[1], genome.json_encode_genome(bl)

    def cmd_blend(self, args):
        name, paths, gnm = self.blend(args)
        out = args.out or ('%s.json' % name)
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
