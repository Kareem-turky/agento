// GET /api/product/health -> Product API GET /health only (no credential forwarded).
import { proxyToProduct } from "../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(request: Request): Promise<Response> {
  return proxyToProduct(request, "health", { authorization: false });
}
