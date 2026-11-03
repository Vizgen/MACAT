# Correction tools

One file per tool. A tool is a plain descriptor object; `index.js` lists them and
that list is the only registration point. To **add** a tool write a file here and
add it to `TOOLS`; to **remove** one delete the file and its line. Nothing else in
the app names a tool.

Every hook is optional. All of them receive `ed`, the editor context that
`CurationStep` builds (see `editorContext` there) — that object is the entire API
a tool is allowed to use, so a tool can never reach into the component's internals.

| hook | when | notes |
| --- | --- | --- |
| `initState()` | once | tool-local React state; reached as `ed.st`, written with `ed.setSt(patch)` |
| `onDown/onMove/onUp(ed, evt, sx, sy)` | pointer | return `true` if handled; falling through lets the host pan or drag a node |
| `draw(ed, ctx)` | every frame | canvas overlay, drawn above the atlas boundaries |
| `cursor(ed, mx, my)` | hover | CSS cursor string, or nothing to fall through |
| `blurb` | render | one static sentence describing the tool, listed in the Correction tool help tip |
| `Panel({ ed })` | render | the sidebar controls for this tool |
| `badges(ed)` | render | array of `{ key, text }` shown in Local correction |
| `blocks(ed)` | render | a string to disable Confirm and explain why, else `null` |
| `snapshot(ed)` / `restore(ed, snap)` | undo | participate in the history stack |
| `interceptUndo(ed)` | undo | return `true` to consume the undo yourself (e.g. cancel a half-finished pair) |

Two rules worth keeping:

- **Tools never persist their own format.** Every gesture must end by writing the
  shared control lattice (`ed.grid().dst`) and calling `ed.revalidate()`. The
  backend validates that lattice and has no idea which tool produced it, which is
  why a new tool needs no server change at all.
- **Validate before you commit a gesture.** `ed.revalidate()` returns the verdict;
  if it is not `ok`, restore and pop the history entry you pushed.
