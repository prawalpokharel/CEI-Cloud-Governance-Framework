import { NextResponse } from 'next/server';

// The NIW surface lives at /niw; the capitalised /NIW appears in written
// materials, so it must resolve too. This cannot be a next.config.js
// redirect: those match case-insensitively, so a '/NIW' source also matches
// '/niw' and the redirect chases its own tail. Middleware sees the pathname
// verbatim, so only the exact capitalised form is rewritten.
export function middleware(request) {
  if (request.nextUrl.pathname === '/NIW') {
    const url = request.nextUrl.clone();
    url.pathname = '/niw';
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

// The matcher is a coarse filter (it may match either case); the exact
// comparison above is what decides.
export const config = { matcher: '/NIW' };
