# Flockutil

Flockutil helps you manage a flock of fractal flames as a Git repository. Yeah,
it's a mixed metaphor. Get over it.

## Dependencies

Currently, flockutil depends on [cuburn][], and (apart from the `convert`
command) only speaks its JSON-based dialect for representing fractal flame
animations. While both projects are used in something resembling production,
and the latest revisions at any given time should work together, no stable
releases have been made. Flockutil includes both itself and cuburn as
submodules in a given repository to ameliorate this somewhat. (In the future, a
render farm might be provided as part of the Aduro project, so you won't need a
full working cuburn setup to render.)

## Getting started

Flockutil will clone itself and cuburn when creating a repository, so you can
just run this command to initialize a repository:

    wget -O - https://raw.github.com/stevenrobertson/flockutil/master/flockutil/main.py | python - init PATH_TO_FLOCK/

You can of course do something similar with a local repository:

    python flockutil/main.py init PATH/

Now `cd` to the new flock repo, and run

    ./flock convert gnm.flam3 [...]
    git commit

to import some existing `flam3`-format edges. From here, you can use the
`blend` and more automated `addEdges` commands to create transitions between
the reference nodes you've added by hand, `evolve` to kick off a genetic
algorithm-based search for optimal edges, and `render` to see the results. Of
course, very few of these things will work reliably or consistently yet; this
is all still very much experimental.

[cuburn]: http://github.com/stevenrobertson/cuburn
