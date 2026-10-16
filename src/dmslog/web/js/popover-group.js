// One popover open at a time across the strip (UI-18): the "Filters" form, the "+N more" list and the "..." menu are
// separate components; each one joins the group and tells it when it opens and closes. No DOM here.

/** Returns {join(id, close) -> {opened(), closed()}}. Opening a member closes the one that was open. */
export function createPopoverGroup() {
  const closers = new Map();
  let current = null;
  return {
    join(id, close) {
      closers.set(id, close);
      return {
        opened() {
          const previous = current;
          current = id;
          if (previous !== null && previous !== id) {
            const closePrevious = closers.get(previous);
            if (closePrevious) {
              closePrevious();
            }
          }
        },
        closed() {
          if (current === id) {
            current = null;
          }
        },
      };
    },
  };
}
