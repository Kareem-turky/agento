// GET /api/product/knowledge/operating-model/versions -> Product API GET
// /api/v1/knowledge/operating-model/versions (immutable version history metadata).
import { proxyToProduct } from "../../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(request: Request): Promise<Response> {
  return proxyToProduct(request, "knowledgeOperatingModelVersions", { authorization: true });
}
