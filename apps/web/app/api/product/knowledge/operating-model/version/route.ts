// GET /api/product/knowledge/operating-model/version?version= -> Product API GET
// /api/v1/knowledge/operating-model/version (one immutable version). Publishing a new
// operating model is API-first: this console does not proxy the publish route.
import { proxyToProduct } from "../../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(request: Request): Promise<Response> {
  return proxyToProduct(request, "knowledgeOperatingModelVersion", { authorization: true, query: ["version"] });
}
