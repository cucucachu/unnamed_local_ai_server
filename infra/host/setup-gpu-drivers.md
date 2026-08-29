# GPU driver verification (Radeon 890M / gfx1150, Vulkan)

This is a doc, not a script — GPU/driver setup is host-specific enough (kernel
version, exact silicon, whether Mesa is already current) that a one-shot
script is more likely to do the wrong thing than to help. Run the checks
below manually, in order.

Background: the project uses **Vulkan (RADV/Mesa)**, not ROCm, for inference —
see the README's Architecture section for why (ROCm only sees VRAM, Vulkan
sees VRAM+GTT via `VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT`, which matters because
the 890M has no dedicated VRAM and shares system RAM).

## 1. Check the kernel version

gfx1150 (Strix Point / Radeon 880M/890M) support landed progressively in
`amdgpu`; kernel **≥ 6.10** is recommended.

```bash
uname -r
```

## 2. Confirm the `amdgpu` kernel driver is loaded

```bash
lsmod | grep amdgpu
lspci -nn | grep -i -E "vga|display|3d"   # should show AMD/ATI Strix [Radeon 880M/890M]
```

## 3. Confirm the DRM device nodes exist

```bash
ls -la /dev/dri
```

You should see `card*` (owned by group `video`) and `renderD*` (owned by
group `render`). If `/dev/dri` is empty or missing, the kernel driver isn't
binding to the GPU — check `dmesg | grep -i amdgpu` for firmware-loading
errors before going further.

## 4. Install Mesa/Vulkan userspace (Ubuntu 24.04+)

```bash
sudo apt update
sudo apt install -y mesa-vulkan-drivers vulkan-tools
```

`mesa-vulkan-drivers` provides the `radv` Vulkan ICD; `vulkan-tools` provides
`vulkaninfo`, used to verify it below.

## 5. Add your user to `render` and `video`

The device nodes in step 3 are group-owned, not world-accessible:

```bash
sudo usermod -aG render,video "$USER"
```

**Log out and back in (or reboot)** for the new group membership to take
effect in your shell — `id` should then list both `render` and `video`.

## 6. Verify Vulkan sees the GPU

```bash
vulkaninfo --summary
```

Look for an entry with `driverID = DRIVER_ID_MESA_RADV` and
`deviceName = AMD Radeon 890M Graphics (RADV STRIX1)` (exact device string
varies by Mesa version, but it should clearly name the Radeon 880M/890M, not
just `llvmpipe`, which is the CPU software-rasterizer fallback and means the
real GPU wasn't found).

Ignore `Code 0 : ICD ... does not export vkGetPhysicalDeviceDisplay*` warnings
— those are display/output-plane extensions irrelevant to headless inference.

## 7. Get `RENDER_GID` / `VIDEO_GID` for `.env`

`model-runner`'s container needs `group_add` set to the **host's** GIDs for
these groups so it can open `/dev/dri` after passthrough:

```bash
getent group render | cut -d: -f3
getent group video  | cut -d: -f3
```

Put those numbers in `.env` as `RENDER_GID` / `VIDEO_GID`.

## Troubleshooting

- **`vulkaninfo` only lists `llvmpipe`**: Mesa's Vulkan ICD isn't finding the
  GPU. Re-check steps 2–4; a `lspci -k` showing `Kernel driver in use: amdgpu`
  is the key signal.
- **`vulkaninfo` hangs or errors with a permissions message**: you're not
  actually in `render`/`video` yet in the *current* shell — re-login, don't
  just re-run `usermod`.
- **Later, inside the `model-runner` container**: the container needs
  `--device /dev/dri` (or the compose `devices:` equivalent) *and*
  `group_add: ["${RENDER_GID}", "${VIDEO_GID}"]` — device passthrough alone
  isn't sufficient if the container's runtime user isn't in a group that owns
  those nodes.
