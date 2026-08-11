import { NextResponse } from 'next/server';

export const maxDuration = 300; // Allow up to 5 minutes

export async function POST(req) {
  try {
    const formData = await req.formData();
    // Default to the localhost backend port if no environment variable is set
    const backendUrl = process.env.BACKEND_URL || 'http://127.0.0.1:8000';
    
    // Use the native fetch API to bypass next-http-proxy-middleware 30s timeout
    const response = await fetch(`${backendUrl}/api/purchase-item/extract`, {
      method: 'POST',
      body: formData,
      // Node.js native fetch has no timeout by default
    });
    
    if (!response.ok) {
      const errorText = await response.text();
      return NextResponse.json({ error: errorText || "Backend error" }, { status: response.status });
    }
    
    const data = await response.json();
    return NextResponse.json(data);
  } catch (error) {
    console.error("[extract-proxy] Error proxying to backend:", error);
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
