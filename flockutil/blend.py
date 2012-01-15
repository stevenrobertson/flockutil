#!/usr/bin/python

# Copyright 2011 Eric Reckase <e.reckase@gmail.com>.

from copy import deepcopy
import numpy as np
from scipy.ndimage.filters import gaussian_filter1d

from cuburn import genome
from cuburn.genome import SplEval
from cuburn.code.interp import Palette

pad_arg = 'normal', 'flipped'

normal_affine = dict(spread=45, magnitude={'x':1, 'y':1},
                     angle=45, offset={'x':0, 'y':0})
flipped_affine = dict(spread=-45, magnitude={'x':1, 'y':1},
                      angle=135, offset={'x':0, 'y':0})

def blend_genomes(left, right, nloops=2, align='weightflip', seed=None,
        stagger=False, blur=None, palflip=True):
    """
    Blend two genomes. Returns the blended genome dictionary as an _AttrDict.

    ``left`` and ``right`` are the respective source genomes.

    ``num_loops`` is the number of complete loops through which xforms will be
    rotated. Use at least two to avoid singularities and backwards rotations.

    ``align`` changes the sort applied before aligning xforms.

    ``seed`` is the seed for the random number generator. If not supplied, a
    hash of the combined xform name (from the ``info`` genome section, not
    from filenames) will be used.

    ``stagger`` will be changed soon, don't count on it.

    ``blur``, if set to a positive value, will spatially blur the color
    palettes with a standard deviation of that value, and insert the blurred
    palettes near (but not at) the start and end of the blended edge.

    ``palflip``, if True, will flip the palette of the right node in
    color-coordinate space, as well as the colors of all xforms, when it is
    determined that this will result in a smaller overall change in color
    coordinates throughout the edge. This reduces the appearance of abrupt
    changes in hue during the edge.
    """
    left, right = map(genome._AttrDict, (left, right))
    align_xforms(left, right, align)
    name = '%s=%s' % (left.info.get('name', ''), right.info.get('name', ''))
    if seed is None:
        seed = map(ord, name)
    rng = np.random.RandomState(seed)

    blend = blend_splines(left, right, nloops, rng, stagger)
    # TODO: licenses; check license compatibility when merging
    # TODO: add URL and flockutil revision to authors
    blend['info'] = {
            'name': name,
            'authors': sum([g.info.get('authors', []) for g in left, right], [])
        }
    blend['info']['authors'].append('flockutil')
    blend['palettes'] = [get_palette(left, False), get_palette(right, True)]

    if palflip:
        checkpalflip(blend)

    if blur:
        blur_palettes(blend, blur)

    return blend

def blend_splines(A, B, nloops, rng, stagger=False):
    """
    Blend the splines of two aligned genomes. Returns a new top-level
    genome dict.
    """
    da, db = A.time.duration, B.time.duration
    if isinstance(da, basestring) ^ isinstance(db, basestring):
        raise ValueError('Cannot blend between relative- and absolute-time')
    da, db = [float(d[:-1]) if isinstance(d, basestring) else d
              for d in (da, db)]
    dc = (da + db) * nloops / 2.0
    scalea, scaleb = dc / da, dc / db

    def go(a, b, path=()):
        if isinstance(a, dict):
            try:
                return dict((k, go(a[k], b[k], path+(k,))) for k in sorted(a))
            except KeyError, e:
                e.args = path[-1:] + e.args
                raise e
        elif isinstance(a, SplEval):
            ik = lambda nl: blend_spline(a, b, scalea, scaleb, nloops=nl,
                                         rng=rng, stagger=stagger)
            # interpolate a with b (it will exist)
            if path[-2:] == ('affine', 'angle'):
                if path[-3] != 'final':
                    if abs(a(1, 1)) < 1e-6 and abs(b(0, 1)) < 1e-6:
                        return ik(0)
                    elif abs(a(1, 1)) < 1e-6 or abs(b(0, 1)) < 1e-6:
                        return ik(1)
                    else:
                        return ik(nloops)
                else:
                    return ik(0)
            elif path[-2:] in [('post', 'angle'), ('camera', 'rotation')]:
                return ik(0)
            return ik(None)
        elif path == ('color', 'palette_times'):
            # TODO: more advanced specification of palette interpolation
            return [0.0, "0", 1.0, "1"]
        else:
            return a

    C = go(A, B)
    C['time']['duration'] = '%gs' % dc if isinstance(da, basestring) else dc
    return C

def get_palette(g, right):
    v = g['color']['palette_times']
    if isinstance(v, basestring):
        v = int(v)
    elif right:
        v = int(v[1])
    else:
        v = int(v[-1])
    return g['palettes'][v]

def isflipped(xf):
    return xf['spread'](0) < 0

def blend_spline(ka, kb, scalea, scaleb, nloops=None,
                 stagger=False, rng=None):
    """
    Blend a pair of splines. Returns a new SplEval.

    ``ka`` and ``kb`` are the left and right SplEval splines.

    ``scalea`` and ``scaleb`` are the ratios of the blended duration over the
    left and right durations, used to scale velocities.

    ``nloops``, if not None, indicates that ``ka`` and ``kb`` represent an
    angle in degrees, and specifies the number of clockwise rotations that
    should be taken in this path. Specifically, the difference between the
    start and end values will satisfy ``-180 <= diff + 360 * nloops <= 180``.

    ``stagger`` and ``rng`` will be replaced soon.
    """
    vala, slopea = ka(1), ka(1, deriv=1) * scalea
    valb, slopeb = kb(0), kb(0, deriv=1) * scaleb

    if nloops is not None:
        vala, valb = vala % 360, valb % 360
        valb = vala + ((valb - vala + 180) % 360) - 180
        valb += nloops * -360

    knots = [0.0, vala, 1.0, valb]
    if stagger:
        knots = [0.0, vala,
                 0.05, vala + 0.05 * (slopea + rng.uniform(high=0.2) * (valb - vala)),
                 0.95, valb - 0.05 * (slopeb + rng.uniform(high=0.2) * (valb - vala)),
                 1.0, valb]
    spl = SplEval(knots, slopea, slopeb)
    return spl

def create_pad_xform(xin, ptype='normal', posttype='normal', final=False):
    # Create a new xform to pad with.
    # As long as this isn't a final xform, this can be anything at all
    #  since the weight goes to 0.  Stick to linear for the moment.
    xout = dict(color=xin['color'], color_speed=xin['color_speed'],
                opacity=xin['opacity'])
    if not final:
        xout['density'] = 0

    # TODO: chaos
    #if final == 'false':
        #xout['chaos'] = dict()

    if ptype == 'normal':
        xout['affine'] = normal_affine.copy()
    else:
        xout['affine'] = flipped_affine.copy()

    if 'post' in xin:
        if posttype == 'normal':
            xout['post'] = normal_affine.copy()
        else:
            xout['post'] = flipped_affine.copy()

    # If xin contains any of these, use the inverse identity
    HOLES = ['spherical', 'ngon', 'julian', 'juliascope', 'polar', 'wedge_sph',
             'wedge_julia', 'bipolar']

    xout['variations'] = dict()

    # Check for HOLES
    for v in HOLES:
        if v in xin['variations']:
            xout['variations']['linear'] = dict(weight=-1)
            xout['affine']['angle'] += 180
            return xout

    # See if xin has types that can be made into identity using parameters
    if 'rectangles' in xin['variations']:
        xout['variations']['rectangles'] = dict(weight=1.0, x=0.0, y=0.0)
    if 'rings2' in xin['variations']:
        xout['variations']['rings2'] = dict(weight=1.0, val=0.0)
    if 'fan2' in xin['variations']:
        xout['variations']['fan2'] = dict(weight=1.0, x=0.0, y=0.0)
    if 'blob' in xin['variations']:
        xout['variations']['blob'] = dict(weight=1.0, low=1.0, high=1.0, waves=1.0)
    if 'perspective' in xin['variations']:
        xout['variations']['perspective'] = dict(weight=1.0, angle=0.0,
                dist=xin['variations']['perspective']['dist'])
    if 'curl' in xin['variations']:
        xout['variations']['curl'] = dict(weight=1.0, c1=0.0, c2=0.0)
    if 'super_shape' in xin['variations']:
        xout['variations']['super_shape'] = dict(weight=1.0, n1=2.0, n2=2.0,
                n3=2.0, rnd=0.0, holes=0.0,
                m=xin['variations']['super_shape']['m'])

    numvars = len(xout['variations'].keys())
    if numvars > 0:
        for v in xout['variations'].keys():
            xout['variations'][v]['weight'] /= numvars
        return xout

    # set as linear if nothing else specified
    xout['variations']['linear'] = dict(weight=1.0)

    return xout

def sort_xforms(xf, method, t):
    if method == 'natural':
        xf = [xf[k] for k in sorted(xf, key=int)]
    elif method in ('weight', 'weightflip'):
        xf = sorted(xf.values(), key=lambda v: v['density'](t))
        if not (method == 'weightflip' and t == 0):
            xf.reverse()
    elif method == 'color':
        xf = sorted(xf.values(), key=lambda v: v['color'](t))
    else:
        assert 'Unknown method %s' % method

    out = [ [],[],[],[] ]
    for x in xf:
        ix = isflipped(x['affine'])
        if 'post' in x:
            ix += 2 * isflipped(x['post'])
        out[ix].append(x)

    return xf, out

def align_xforms(A, B, sort='weightflip'):
    """
    Aligns the xforms of the genomes A and B in place.
    """
    # make lists of the xform dicts for sorting
    Ax, Bx = A['xforms'], B['xforms']
    Afinal = Ax.pop('final', None)
    Bfinal = Bx.pop('final', None)

    for fin in (Afinal, Bfinal):
        if fin:
            assert not isflipped(fin['affine'])
            assert 'post' not in fin or not isflipped(fin['post'])

    Ax, Ax_sorted = sort_xforms(Ax, sort, 1)
    Bx, Bx_sorted = sort_xforms(Bx, sort, 0)

    # pad each category to have the same number of xforms
    for i in range(4):

        # for things that are already aligned, we must make sure that
        # if a post is present on one side, it's also present on the other
        # this check is only for 'normal' post cases, since those might not
        # be specified
        if i==0 or i==1:
            num_aligned = min(len(Ax_sorted[i]), len(Bx_sorted[i]))
            for xi in range(num_aligned):
                if 'post' in Ax_sorted[i][xi] and 'post' not in Bx_sorted[i][xi]:
                    Bx_sorted[i][xi]['post'] = normal_affine.copy()
                if 'post' in Bx_sorted[i][xi] and 'post' not in Ax_sorted[i][xi]:
                    Ax_sorted[i][xi]['post'] = normal_affine.copy()

        numpad = len(Ax_sorted[i]) - len(Bx_sorted[i])

        if numpad<0:
            padme = Ax_sorted[i]
            useme = Bx_sorted[i]
        else:
            padme = Bx_sorted[i]
            useme = Ax_sorted[i]
            numpad = -numpad

        while numpad<0:
            pad_xform = create_pad_xform(useme[numpad], ptype=pad_arg[i % 2],
                                    posttype=pad_arg[i / 2])
            padme.append(pad_xform)
            numpad += 1

    A_xforms, B_xforms = [dict((str(i), v) for i, v in enumerate(sum(xf,[])))
                          for xf in (Ax_sorted, Bx_sorted)]

    # TODO: restore chaos
    for i in range(0): #len(A_xforms)):
        old_chaos_A = A_xforms[str(i)]['chaos']
        old_chaos_B = B_xforms[str(i)]['chaos']

        new_chaos_A = dict([(A_remap[k], old_chaos_A[k]) for k in old_chaos_A])
        new_chaos_B = dict([(B_remap[k], old_chaos_B[k]) for k in old_chaos_B])

        if len(new_chaos_A.keys()) == 0:
            new_chaos_A = new_chaos_B

        if len(new_chaos_B.keys()) == 0:
            new_chaos_B = new_chaos_A

        # now if there are missing keys, fill them with 1's
        for j in range(0,len(A_xforms)):
            if str(j) not in new_chaos_A:
                new_chaos_A[str(j)] = 1
            if str(j) not in new_chaos_B:
                new_chaos_B[str(j)] = 1

        A_xforms[str(i)]['chaos'] = new_chaos_A
        B_xforms[str(i)]['chaos'] = new_chaos_B

    if Afinal and Bfinal:
        A_xforms['final'] = Afinal
        B_xforms['final'] = Bfinal
    elif Afinal or Bfinal:
        useme = Afinal if Afinal else Bfinal
        padfinal = create_pad_xform(useme, ptype='normal', final=True)
        # color_speed of final xform pad must be 0
        padfinal['color_speed'] = 0.0
        if Afinal:
            A_xforms['final'] = Afinal
            B_xforms['final'] = padfinal
        else:
            A_xforms['final'] = padfinal
            B_xforms['final'] = Bfinal

    # now make sure we have the same variations on each side
    for xf in A_xforms.keys():
        Axf = A_xforms[xf]
        Bxf = B_xforms[xf]
        allvars = set(Axf['variations'].keys()).union(Bxf['variations'].keys())
        for var in allvars:
            if var not in Axf['variations']:
                Axf['variations'][var] = Bxf['variations'][var].copy()
                Axf['variations'][var]['weight'] = 0.0
            if var not in Bxf['variations']:
                Bxf['variations'][var] = Axf['variations'][var].copy()
                Bxf['variations'][var]['weight'] = 0.0

    A['xforms'], B['xforms'] = map(genome._AttrDict._wrap, (A_xforms, B_xforms))

def checkpalflip(gnm):
    if 'final' in gnm['xforms']:
        f = gnm['xforms']['final']
        fcv, fcsp = f['color'], f['color_speed']
    else:
        fcv, fcsp = SplEval(0), SplEval(0)
    sansfinal = [v for k, v in gnm['xforms'].items() if k != 'final']

    lc, rc = [np.array([v['color'](t) * (1 - fcsp(t)) + fcv(t) * fcsp(t)
               for v in sansfinal]) for t in (0, 1)]
    rcrv = 1 - rc
    # TODO: use spline integration instead of L2
    dens = np.array([np.hypot(v['density'](0), v['density'](1))
                     for v in sansfinal])
    if np.sum(np.abs(dens * (rc - lc))) > np.sum(np.abs(dens * (rcrv - lc))):
        palflip(gnm)

def palflip(gnm):
    for v in gnm['xforms'].values():
        c = v['color']
        v['color'] = SplEval([0, c(0), 1, 1 - c(1)], c(0, 1), -c(1, 1))
    pal = genome.palette_decode(gnm['palettes'][1])
    gnm['palettes'][1] = genome.palette_encode(np.flipud(pal))

def blur_palettes(gnm, stdev):
    assert len(gnm['palettes']) == 2
    gnm['palettes'].extend([create_blurred_palette(p, stdev)
                            for p in gnm['palettes']])
    gnm['color']['palette_times'] = [0.0, '0', 0.1, '2', 0.9, '3', 1.0, '1']

def create_blurred_palette(enc_palette, stdev):
    pal = genome.palette_decode(enc_palette)
    y, uvr, uvt, a = Palette.rgbtoyuvpolar(pal)
    uvt = gaussian_filter1d(uvt, stdev)
    y, uvr, a = [gaussian_filter1d(ch, stdev * 0.5) for ch in y, uvr, uvt]
    return genome.palette_encode(Palette.yuvpolartorgb(y, uvr, uvt, a))
