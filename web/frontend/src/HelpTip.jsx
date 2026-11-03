// A "?" beside a section heading, shared by the curation sidebar and the
// Results tables. Lifted out of CurationStep.jsx on 2026-09-18 when Results
// needed one too. The placement maths is in ./helptipPlacement.js -- NOT
// "./helptip.js", which would differ from this file's name only by case and so
// would both break extensionless resolution and be a hazard on a
// case-insensitive checkout.
import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { helpTipPosition } from "./helptipPlacement";

// A "?" beside a sidebar heading. What it holds is orientation -- true every
// time but worth reading once -- so it is one hover away rather than permanently
// above the control it describes. `tabIndex` makes it reachable without a mouse.
//
// The panel is `position: fixed` and placed from JS rather than being an
// absolutely-positioned child. It has to be: the sidebar is `overflow-y: scroll`,
// and per spec a non-`visible` overflow on one axis forces the other to `auto`
// too, so anything wider than the 320px panel gets CLIPPED at its edge. Fixed
// positioning leaves that formatting context entirely and lets the tip sit over
// the canvas, which is the only way to give it a readable line length.
function HelpTip({ label, children }) {
  const anchor = useRef(null);
  const body = useRef(null);
  const [at, setAt] = useState(null);

  const view = () => ({ width: window.innerWidth, height: window.innerHeight });
  const show = () => {
    const r = anchor.current?.getBoundingClientRect();
    if (!r) return;
    setAt({ ...helpTipPosition(r, view()), placed: false });
  };
  const hide = () => setAt(null);

  // Flip above the icon when the tip would run off the bottom of the window --
  // which the lower headings in a long sidebar otherwise do. Height is not known
  // until it has rendered, hence the measure-then-adjust pass; `placed` stops
  // that from looping.
  useLayoutEffect(() => {
    if (!at || at.placed || !body.current) return;
    const b = body.current.getBoundingClientRect();
    const r = anchor.current?.getBoundingClientRect();
    if (!r) return;
    setAt({ ...helpTipPosition(r, view(), b.height), placed: true });
  }, [at]);

  // A tip opened by keyboard would otherwise hang in place while the panel
  // scrolls out from under it.
  useEffect(() => {
    if (!at) return undefined;
    window.addEventListener("scroll", hide, true);
    return () => window.removeEventListener("scroll", hide, true);
  }, [at]);

  return (
    <span className="helptip" ref={anchor} tabIndex={0} role="note" aria-label={label}
          onMouseEnter={show} onMouseLeave={hide} onFocus={show} onBlur={hide}>
      ?
      {at && (
        <span className="helptip-body" ref={body}
              style={{ left: at.left, top: at.top,
                       visibility: at.placed ? "visible" : "hidden" }}>
          {children}
        </span>
      )}
    </span>
  );
}

export default HelpTip;
