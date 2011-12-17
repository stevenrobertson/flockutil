#!/usr/bin/python

import base64
import xml.parsers.expat
import numpy as np

from cuburn.code.variations import var_code, var_params
from cuburn.genome import json_encode_genome

class GenomeParser(object):
    def __init__(self):
        self.flames = []
        self._flame = None
        self.parser = xml.parsers.expat.ParserCreate()
        self.parser.StartElementHandler = self.start_element
        self.parser.EndElementHandler = self.end_element

    def parse(self, file):
        self.parser.ParseFile(file)

    def start_element(self, name, attrs):
        if name == 'flame':
            assert self._flame is None
            self._flame = dict(attrs)
            self._flame['xforms'] = []
            self._flame['palette'] = np.zeros((256, 3), dtype=np.uint8)
        elif name == 'xform':
            self._flame['xforms'].append(dict(attrs))
        elif name == 'finalxform':
            self._flame['finalxform'] = dict(attrs)
        elif name == 'color':
            idx = int(attrs['index'])
            self._flame['palette'][idx] = map(int, attrs['rgb'].split())
    def end_element(self, name):
        if name == 'flame':
            self.flames.append(self._flame)
            self._flame = None

def convert_flame(flame):
    cvt = lambda ks: dict((k, float(flame[k])) for k in ks)
    camera = {
        'center': dict(zip('xy', map(float, flame['center'].split()))),
        'scale': float(flame['scale']) / float(flame['size'].split()[0]),
        'dither_width': float(flame['filter']),
        'rotation': float(flame['rotate']),
        'density': 1.0
    }

    info = {}
    for k, f in [('name', 'name'), ('author_url', 'url'), ('author', 'nick')]:
        if f in flame:
            info[k] = flame[f]

    time = dict(frame_width=float(flame.get('temporal_filter_width', 1)),
                duration=1)

    color = cvt(['brightness', 'gamma'])
    color.update((k, float(flame.get(k, d))) for k, d in
                 [('highlight_power', -1), ('gamma_threshold', 0.01)])
    color['vibrance'] = float(flame['vibrancy'])
    color['background'] = dict(zip('rgb',
                               map(float, flame['background'].split())))
    color['palette_times'] = "0"

    de = dict((k, float(flame.get(f, d))) for f, k, d in
                [('estimator', 'radius', 11),
                 ('estimator_minimum', 'minimum', 0),
                 ('estimator_curve', 'curve', 0.6)])

    xfs = dict(enumerate(map(convert_xform, flame['xforms'])))
    if 'finalxform' in flame:
        xfs['final'] = convert_xform(flame['finalxform'], True)

    pal = base64.b64encode(flame['palette'])
    pals = [['rgb8'] + [pal[i:i+64] for i in range(0, len(pal), 64)]]
    return dict(camera=camera, color=color, de=de, xforms=xfs,
                info=info, time=time, palettes=pals, link='self')

def convert_xform(xf, isfinal=False):
    # TODO: chaos
    xf = dict(xf)
    symm = float(xf.pop('symmetry', 0))
    anim = xf.pop('animate', symm >= 0)
    out = dict((k, float(xf.pop(k, v))) for k, v in
               dict(color=0, color_speed=(1-symm)/2, opacity=1).items())
    if not isfinal:
        out['density'] = float(xf.pop('weight'))
    out['affine'] = convert_affine(xf.pop('coefs'), anim)
    if 'post' in xf and map(float, xf['post'].split()) != [1, 0, 0, 1, 0, 0]:
        out['post'] = convert_affine(xf.pop('post'))
    out['variations'] = {}
    for k in var_code:
        if k in xf:
            var = dict(weight=float(xf.pop(k)))
            for param, default in var_params.get(k, {}).items():
                var[param] = float(xf.pop('%s_%s' % (k, param), default))
            out['variations'][k] = var
    assert not xf, 'Unrecognized parameters remain: ' + xf
    return out

def convert_affine(aff, animate=False):
    xx, yx, xy, yy, xo, yo = map(float, aff.split())
    # Invert all instances of y (yy is inverted twice)
    yx, xy, yo = -yx, -xy, -yo

    xa = np.degrees(np.arctan2(yx, xx))
    ya = np.degrees(np.arctan2(yy, xy))
    xm = np.hypot(xx, yx)
    ym = np.hypot(xy, yy)

    angle_between = ya - xa
    if angle_between < 0:
        angle_between += 360

    if angle_between < 180:
        spread = angle_between / 2.0
    else:
        spread = -(360-angle_between) / 2.0

    angle = xa + spread
    if angle < 0:
        angle += 360.0

    if animate:
        angle = [0, angle, 1, angle - 360]

    return dict(spread=spread, magnitude={'x': xm, 'y': ym},
                angle=angle, offset={'x': xo, 'y': yo})

def convert_file(path):
    p = GenomeParser()
    p.parse(open(path))
    for flame in p.flames:
        yield convert_flame(flame)

if __name__ == "__main__":
    import sys
    print '\n\n'.join(map(json_encode_genome, convert_file(sys.argv[1])))
