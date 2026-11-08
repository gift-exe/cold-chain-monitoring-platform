// Lists and tables shown 15 rows at a time (E9, P22, P26): the incidents and shipments lists, section by
// section, and the administration tables. If the selected item is on another page (for example, opened
// from a link), that page is shown.
import { useEffect, useState } from "react";

export const PAGE_SIZE = 15;

// The rows of the current page. `selectedIndex` (or -1) brings its page into view; `resetKey` (e.g. a
// search) goes back to the first page when it changes.
export function usePage(items, { selectedIndex = -1, resetKey } = {}) {
  const pages = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
  const [page, setPage] = useState(0);
  useEffect(() => {
    if (selectedIndex >= 0) setPage(Math.floor(selectedIndex / PAGE_SIZE));
  }, [selectedIndex >= 0 ? Math.floor(selectedIndex / PAGE_SIZE) : -1]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setPage(0); }, [resetKey]);
  useEffect(() => {
    if (page > pages - 1) setPage(pages - 1);              // the list got shorter
  }, [pages, page]);
  const first = page * PAGE_SIZE;
  return { rows: items.slice(first, first + PAGE_SIZE), page, pages, first, total: items.length, setPage };
}

export function Pager({ pg, label }) {
  if (pg.pages <= 1) return null;
  return (
    <nav className="pager" aria-label={`${label} pages`}>
      <span className="muted">{pg.first + 1}–{pg.first + pg.rows.length} of {pg.total}</span>
      <div className="btn-row">
        <button className="btn btn-small" disabled={pg.page === 0} onClick={() => pg.setPage(pg.page - 1)}>Previous</button>
        <span className="pager-n">Page {pg.page + 1} of {pg.pages}</span>
        <button className="btn btn-small" disabled={pg.page >= pg.pages - 1} onClick={() => pg.setPage(pg.page + 1)}>Next</button>
      </div>
    </nav>
  );
}

export default function PagedList({ items, getKey, selectedKey, render, label }) {
  const selectedIndex = selectedKey == null ? -1 : items.findIndex((i) => getKey(i) === selectedKey);
  const pg = usePage(items, { selectedIndex });
  return (
    <>
      <ul className="inc-rows">{pg.rows.map(render)}</ul>
      <Pager pg={pg} label={label} />
    </>
  );
}
