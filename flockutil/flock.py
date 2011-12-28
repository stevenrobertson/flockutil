#!/usr/bin/env python2

import os
import sys
import json
import random
import shutil
import warnings
from os.path import isfile, join
from glob import glob
from hashlib import sha1
from tempfile import mkdtemp
from subprocess import check_output, CalledProcessError, STDOUT
from contextlib import contextmanager
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

    def list_flock(self, shuffle=False, rating=True, separate=False,
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
                    self.start_log(odir, name, rev, times, prof)

                rt = list(enumerate(times, 1))
                rt = rt[::(prof['skip']+1)*(2**(args.passes-p-1))]
                if args.randomize:
                    np.random.shuffle(rt)

                if rev != 'untracked':
                    llink = join('out', args.profile, edge, 'latest')
                    if os.path.islink(llink):
                        os.unlink(llink)
                    os.symlink(rev, llink)
                    rt = ifilter(lambda r: not os.path.isfile(topath(r[0])), rt)
                self.render_frames(odir, gnm, prof, rt)

    @staticmethod
    def start_log(odir, name, rev, times, prof):
        with open(join(odir, 'log.txt'), 'w') as fp:
            fp.write('%s rev=%s nf=%d\n' %
                     (name, rev, len(times) / (prof['skip']+1)))

    @staticmethod
    def topath(odir, idx):
        return join(odir, '%05d.jpg' % idx)

    def render_frames(self, odir, gnm, prof, rt):
        import scipy
        import pycuda.autoinit

        renderer = render.Renderer()
        w, h = prof['width'], prof['height']
        for out in renderer.render(gnm, rt, w, h):
            noalpha = out.buf[:,:,:3]
            img = scipy.misc.toimage(noalpha, cmin=0, cmax=1)
            path = self.topath(odir, out.idx)
            img.save(path, quality=95)
            with open(join(odir, 'log.txt'), 'a') as fp:
                # TODO: add unique GPU id, other frame stats
                fp.write('%d g=%d\n' % (out.idx, out.gpu_time))
            print 'Wrote %s (took %5d ms)' % (path, out.gpu_time)

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

    def cmd_update(self, args):
        assert not self.flock.dirty, 'Repository is dirty.'
        edges = self.flock.list_flock()

        # TODO: only use profiles in repo?
        if not args.profiles:
            args.profiles = [p[9:-5] for p in glob('profiles/*.json')]

        for pname in args.profiles:
            prof = json.load(open(join('profiles', pname + '.json')))
            for edge in edges:
                print '\n', pname, edge
                ldir = os.path.realpath(join('out', pname, edge, 'latest'))
                if not os.path.isdir(ldir): continue
                # TODO: determine ext from output format
                idxs = [int(i.rsplit('/', 1)[-1].rsplit('.', 1)[0])
                        for i in glob(ldir + '/*.jpg')]
                if len(idxs) < 10 * args.nframes: continue
                gnm, name, rev = self.load_edge(edge)
                odir = join('out', pname, edge, rev)
                if os.path.isdir(odir): continue

                def cmp(d1, d2, rt, thresh=0):
                    for i, t in rt:
                        # TODO: implement SSIM
                        p1, p2 = self.topath(d1, i), self.topath(d2, i)
                        cmp = check_output(['compare', '-metric', 'RMSE',
                                    p1, p2, '/tmp/ignore.jpg'], stderr=STDOUT)
                        v = float(cmp.split('(')[1].split(')')[0])
                        print 'Frame %05d: %g' % (i, v)
                        if v > thresh:
                            yield ((i, t), v)

                err, times = gnm.set_profile(prof)
                if len(times) < max(idxs): continue
                rt = list(enumerate(times, 1))
                rt = [rt[i-1] for i in random.sample(idxs, args.nframes)]

                with TemporaryDir() as tdir:
                    cp = lambda: shutil.copytree(tdir, odir)

                    print 'Rendering frames for comparaison'
                    self.start_log(tdir, name, rev, times, prof)
                    self.render_frames(tdir, gnm, prof, rt)
                    try:
                        retry = dict(cmp(ldir, tdir, rt, args.diff))
                    except CalledProcessError:
                        cp()
                        continue

                    if retry:
                        print 'Absolute threshold exceeded'
                        if args.reldiff <= 1:
                            cp()
                            continue
                        print 'Computing self-similarity for relative threshold'
                        with TemporaryDir() as tdir2:
                            self.render_frames(tdir2, gnm, prof, retry.keys())
                            retried = dict(cmp(tdir, tdir2, retry.keys()))
                            hi = max(retry[k] / retried[k] for k in retry)
                            print hi
                            if hi > args.reldiff:
                                print 'Relative threshold exceeded (%g)' % hi
                                cp()
                                continue

                print 'Looks good, linking to old genome'
                os.symlink(os.path.relpath(ldir, os.path.dirname(odir)), odir)

@contextmanager
def TemporaryDir():
    dir = mkdtemp()
    yield dir
    shutil.rmtree(dir)
