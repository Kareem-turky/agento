// /api/product/integrations/connections -> Product API /api/v1/integrations/connections
// (GET: list connection metadata; POST: create a connection). Credentials in the POST body
// pass straight through to the Product API and are never stored, cached or logged here.
import { proxyToProduct } from "../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(request: Request): Promise<Response> {
  return proxyToProduct(request, "integrationConnections", { authorization: true });
}

export function POST(request: Request): Promise<Response> {
  return proxyToProduct(request, "integrationConnectionCreate", { authorization: true, body: true });
}
