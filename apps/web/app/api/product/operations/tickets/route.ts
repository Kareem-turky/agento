// POST /api/product/operations/tickets -> Product API POST /api/v1/operations/tickets
// (the explicit, idempotent ticket write).
import { proxyToProduct } from "../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function POST(request: Request): Promise<Response> {
  return proxyToProduct(request, "tickets", {
    authorization: true,
    idempotencyKey: true,
    body: true,
  });
}
