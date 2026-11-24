import { useLocation } from "react-router";

/**
 * The address that starts GitHub sign-in and brings the visitor back to the page they are on.
 * The API checks that the path is on this site, so passing the current location is safe.
 */
export function useSignInHref(): string {
  const { pathname, search } = useLocation();
  return `/api/auth/login?next=${encodeURIComponent(pathname + search)}`;
}
