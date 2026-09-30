// POST /api/product/operations/runs -> Product API POST /api/v1/operations/runs only
// (the read-only Operations analysis; never an AgentOS route).
import { proxyToProduct } from "../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function POST(request: Request): Promise<Response> {
  return proxyToProduct(request, "operationsRuns", { authorization: true, body: true });
}
