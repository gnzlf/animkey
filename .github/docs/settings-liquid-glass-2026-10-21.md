# Settings glass revision

The previous native backdrop path disabled Qt's translucent backing store and
left it without automatic background clearing. Transparent page transitions
could retain old content, while a large painted gradient caused visible bands.

The revision keeps Qt's alpha surface throughout the window's lifetime, clears
damaged pixels with Source composition and invalidates the window after changing
pages. Removed lazy placeholders are explicitly hidden and deleted. Only the
selected page is visible.

The glass has a uniform tint, a single fine rim and native background blur.
Full-window and control gradients were removed. Navigation is a segmented
control; controls use neutral translucent fills, a blue primary action and green
switches. Colored window controls retain their hover growth.

Windows uses the accent composition API with a uniform ABGR tint at 25% opacity,
without layering a second system backdrop. Unsupported platforms keep the solid
fallback. This is an approximation of Apple's material, not Apple's renderer.
The native path does not capture the viewport or run refresh timers.

## Verification

Eight Settings tests passed under Maya 2024 standalone. These include repeated
tab roundtrips with pixel equality, repeated repaints with pixel equality and
alpha checks, lazy-page visibility, switches, shortcuts and window-button
actions. Captures of all pages and the compact layout were reviewed.

A Windows-native test returned `backend=accent`, `alpha=True` and
`native_backdrop=True`. The test could not acquire foreground focus, so a
composited desktop capture was deliberately skipped. QWidget captures do not
include the OS blur; the final material appearance still needs confirmation in
the user's active Maya window. No user scene was edited or saved.

## References

- [Apple Materials](https://developer.apple.com/design/human-interface-guidelines/materials)
- [Qt/Windows accent interoperability reference](https://github.com/zhiyiYo/PyQt-Frameless-Window/blob/master/qframelesswindow/windows/window_effect.py)
