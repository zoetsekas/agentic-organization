#!/bin/sh
# Fill in docker/images.lock, and optionally pin Compose files to the digests.
#
# Two modes:
#   resolve-images.sh              resolve every UNRESOLVED digest in the lock
#   resolve-images.sh --apply FILE rewrite FILE's `image:` tags as tag@digest
#
# Why this is a script and not a committed table of digests: the environment
# this repository was developed in has no Docker daemon and no registry access,
# so every digest in the lock is the literal token UNRESOLVED. This resolves
# them on a machine that can reach the registries. Review the diff before
# committing — a digest is a supply-chain claim, not a formatting change.
set -eu

LOCK="$(dirname "$0")/images.lock"

# Whichever of these exists. crane and skopeo need no daemon, which matters on
# a build agent that has no Docker socket.
digest_of() {
    if command -v crane >/dev/null 2>&1; then
        crane digest "$1"
    elif command -v skopeo >/dev/null 2>&1; then
        skopeo inspect --format '{{.Digest}}' "docker://$1"
    elif command -v docker >/dev/null 2>&1; then
        docker buildx imagetools inspect --format '{{json .Manifest.Digest}}' "$1" \
            | tr -d '"'
    else
        echo "need one of: crane, skopeo, docker" >&2
        return 1
    fi
}

if [ "${1:-}" = "--apply" ]; then
    target="${2:?usage: resolve-images.sh --apply <compose-file>}"
    tmp="$(mktemp)"
    cp "$target" "$tmp"
    # Skip records still unresolved rather than writing the token into a
    # Compose file, where it would fail late and confusingly.
    grep -v '^[[:space:]]*#' "$LOCK" | grep -v '^[[:space:]]*$' | \
    while read -r ref digest _rest; do
        [ "$digest" = "UNRESOLVED" ] && continue
        sed -i "s|image: ${ref}\$|image: ${ref}@${digest}|" "$tmp"
    done
    mv "$tmp" "$target"
    echo "pinned $target to the digests in $LOCK"
    exit 0
fi

out="$(mktemp)"
while IFS= read -r line; do
    case "$line" in
        ''|'#'*) printf '%s\n' "$line" >>"$out"; continue ;;
    esac
    ref="$(printf '%s' "$line" | awk '{print $1}')"
    digest="$(printf '%s' "$line" | awk '{print $2}')"
    plane="$(printf '%s' "$line" | awk '{print $3}')"
    if [ "$digest" != "UNRESOLVED" ]; then
        printf '%s\n' "$line" >>"$out"; continue
    fi
    echo "resolving $ref" >&2
    resolved="$(digest_of "$ref")"
    printf '%-52s %s  %s\n' "$ref" "$resolved" "$plane" >>"$out"
done <"$LOCK"
mv "$out" "$LOCK"
echo "updated $LOCK — review the diff before committing" >&2
