import { useCallback, useEffect, useState } from "react";

/** Load data with `load`, re-running when `deps` change; `reload` refreshes on demand. */
export function useData<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let cancelled = false;
    load()
      .then((value) => !cancelled && (setData(value), setError(null)))
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [...deps, version]);

  const reload = useCallback(() => setVersion((v) => v + 1), []);
  return { data, setData, error, reload };
}

export type Route = "inbox" | "saved" | "deadlines" | "hidden" | "funding" | "sources";
const ROUTES: Route[] = ["inbox", "saved", "deadlines", "hidden", "funding", "sources"];

/** Tiny hash router: #/funding etc. Survives reloads, no dependency. */
export function useRoute(): [Route, (route: Route) => void] {
  const read = () => {
    const hash = window.location.hash.replace("#/", "") as Route;
    return ROUTES.includes(hash) ? hash : "inbox";
  };
  const [route, setRoute] = useState<Route>(read);
  useEffect(() => {
    const onChange = () => setRoute(read());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return [route, (next) => (window.location.hash = `/${next}`)];
}
