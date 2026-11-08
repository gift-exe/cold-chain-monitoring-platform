// A list shown 15 rows at a time (E9, P22), for each section of the incidents and shipments lists. If the
// selected item is on another page (for example, opened from a link), that page is shown.
import { useEffect, useState } from "react";

export const PAGE_SIZE = 15;

export default function PagedList({ items, getKey, selectedKey, render, label }) {
  const pages = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
  const [page, setPage] = useState(0);
  const at = selectedKey == null ? -1 : items.findIndex((i) => getKey(i) === selectedKey);

  useEffect(() => {
    if (at >= 0) setPage(Math.floor(at / PAGE_SIZE));
  }, [selectedKey, at >= 0]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (page > pages - 1) setPage(pages - 1);              // the list got shorter
  }, [pages, page]);

  const first = page * PAGE_SIZE;
  const shown = items.slice(first, first + PAGE_SIZE);
  return (
    <>
      <ul className="inc-rows">{shown.map(render)}</ul>
      {pages > 1 && (
        <nav className="pager" aria-label={`${label} pages`}>
          <span className="muted">{first + 1}–{first + shown.length} of {items.length}</span>
          <div className="btn-row">
            <button className="btn btn-small" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
            <span className="pager-n">Page {page + 1} of {pages}</span>
            <button className="btn btn-small" disabled={page >= pages - 1} onClick={() => setPage(page + 1)}>Next</button>
          </div>
        </nav>
      )}
    </>
  );
}
