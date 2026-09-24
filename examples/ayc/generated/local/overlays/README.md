# Your overlays — ayc (local)

Everything beside this folder is generated and will be rewritten. **This folder
is yours**: the compiler creates it and then never reads or writes inside it.

## How it works here

Any `*.yaml` (or `*.yml`) you put here is merged over the generated stack by
Compose's own layering, in sorted order. The generated `Makefile` builds the
`-f` chain for you, so `make up` already includes your overlays.

```bash
make overlays     # which overlay files are in effect
make config       # the merged stack, overlays applied
make up           # start it, overlays and all
```

## Example — swap an image and add a volume

`overlays/10-local-dev.yaml`:

```yaml
services:
  designer:
    image: my-registry/designer:dev
    volumes:
      - ./src:/app/src:ro
```

Name files so they sort in the order you want them applied: `10-`, `20-`, and
so on. Later files win, which is Compose's rule, not ours.

## What this cannot do

An overlay changes the *deployment*, never the design. It cannot grant an agent
a permission, widen a sandbox or remove an approval — those are resolved once
from the spec into the IR, and an overlay is applied long after. If you need a
different permission, change the design and recompile; a control you can edit
away in a compose file was never a control.
