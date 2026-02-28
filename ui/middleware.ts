import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

// Paths that don't require authentication
const PUBLIC_PATHS = [
  '/login',
  '/login/oauth',
  '/api',        // all API routes — Flask handles its own auth
  '/static',
  '/favicon.ico',
];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // Allow public paths
  if (PUBLIC_PATHS.some(p => pathname === p || pathname.startsWith(p + '/'))) {
    return NextResponse.next();
  }

  // Allow Next.js internals
  if (pathname.startsWith('/_next')) {
    return NextResponse.next();
  }

  // Check for Flask session cookie — present means user is logged in
  const session = request.cookies.get('session');
  if (!session) {
    const url = request.nextUrl.clone();
    url.pathname = '/login';
    url.search = pathname !== '/' ? `?next=${encodeURIComponent(pathname)}` : '';
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon\\.ico).*)'],
};
