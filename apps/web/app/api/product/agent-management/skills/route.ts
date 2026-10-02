// GET /api/product/agent-management/skills -> Product API GET /api/v1/skills/catalog
// (read-only Product Skill/Task metadata; never AgentOS).
import { proxyToProduct } from "../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(request: Request): Promise<Response> {
  return proxyToProduct(request, "skillsCatalog", { authorization: true });
}
