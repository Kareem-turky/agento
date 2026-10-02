// GET /api/product/agent-management/skill?skill_id= -> Product API GET /api/v1/skills/skill
// (read-only Product Skill/Task metadata; never AgentOS).
import { proxyToProduct } from "../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(request: Request): Promise<Response> {
  return proxyToProduct(request, "skillDetail", { authorization: true, query: ["skill_id"] });
}
