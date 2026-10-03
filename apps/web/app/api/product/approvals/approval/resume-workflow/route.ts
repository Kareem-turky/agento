// POST /api/product/approvals/approval/resume-workflow?approval_id= -> Product API POST
// /api/v1/approvals/approval/resume-workflow (the requester continues the linked Workflow).
import { proxyToProduct } from "../../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function POST(request: Request): Promise<Response> {
  return proxyToProduct(request, "approvalResumeWorkflow", { authorization: true, query: ["approval_id"] });
}
