// GET /api/product/agent-management/catalog -> Product API GET /api/v1/agents/catalog
// (installed Product Agents; never AgentOS).
import { proxyToProduct } from "../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(request: Request): Promise<Response> {
  return proxyToProduct(request, "agentsCatalog", { authorization: true });
}
