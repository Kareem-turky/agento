// /api/product/integrations/connection?connection_id= -> Product API
// /api/v1/integrations/connection (GET metadata, PUT name/non-secret config, DELETE).
import { proxyToProduct } from "../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const QUERY = ["connection_id"] as const;

export function GET(request: Request): Promise<Response> {
  return proxyToProduct(request, "integrationConnection", { authorization: true, query: QUERY });
}

export function PUT(request: Request): Promise<Response> {
  return proxyToProduct(request, "integrationConnectionUpdate", { authorization: true, query: QUERY, body: true });
}

export function DELETE(request: Request): Promise<Response> {
  return proxyToProduct(request, "integrationConnectionDelete", { authorization: true, query: QUERY });
}
