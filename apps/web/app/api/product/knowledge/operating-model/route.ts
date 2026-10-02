// GET /api/product/knowledge/operating-model -> Product API GET
// /api/v1/knowledge/operating-model (the current structured operating model; never AgentOS).
import { proxyToProduct } from "../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(request: Request): Promise<Response> {
  return proxyToProduct(request, "knowledgeOperatingModel", { authorization: true });
}
