import { NextResponse } from 'next/server';

export async function POST(req) {
  try {
    const formData = await req.formData();
    // Default to the localhost backend port if no environment variable is set
    const backendUrl = process.env.BACKEND_URL || 'http://127.0.0.1:8000';
    
    // Use the native fetch API. This server-side call bypasses the browser's
    // own fetch (and its auth-header wrapper from AuthContext), so the
    // incoming request's Authorization header has to be forwarded by hand --
    // otherwise the backend's session middleware rejects it with 401.
    const response = await fetch(`${backendUrl}/api/purchase-drafts/async-extract`, {
      method: 'POST',
      body: formData,
      headers: { 'Authorization': req.headers.get('authorization') || '' },
    });
    
    if (!response.ok) {
      const errorText = await response.text();
      return NextResponse.json({ error: errorText || "Backend error" }, { status: response.status });
    }
    
    const data = await response.json();
    return NextResponse.json(data);
  } catch (error) {
    console.error("[async-extract-proxy] Error proxying to backend:", error);
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
