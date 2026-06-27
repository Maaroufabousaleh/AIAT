#!/bin/sh
set -eu

link=/app/data/bin/cliproxyapi
wrapper=/opt/aiat/cliproxyapi-compat-wrapper.sh

if [ ! -e "$link" ]; then
    echo "CLIProxyAPI binary is not installed; nothing to repair."
    exit 0
fi

target="$(readlink -f "$link")"
case "$target" in
    /app/data/bin/cliproxyapi-*/cli-proxy-api) ;;
    *)
        echo "Refusing to patch unexpected CLIProxyAPI target: $target" >&2
        exit 1
        ;;
esac

if grep -q "AIAT_CLIPROXYAPI_COMPAT_WRAPPER" "$target" 2>/dev/null; then
    echo "CLIProxyAPI compatibility wrapper already installed."
    exit 0
fi

if [ "$(head -n 1 "$target" 2>/dev/null || true)" = "#!/bin/sh" ] && [ -e "${target}.real" ]; then
    # Preserve an existing real binary when replacing an older local wrapper.
    rm -f "$target"
else
    mv -f "$target" "${target}.real"
fi
{
    echo '#!/bin/sh'
    echo '# AIAT_CLIPROXYAPI_COMPAT_WRAPPER'
    tail -n +2 "$wrapper"
} > "$target"
chmod 0755 "$target" "${target}.real"
echo "Installed CLIProxyAPI -c to -config compatibility wrapper."
