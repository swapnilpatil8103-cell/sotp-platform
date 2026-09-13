import {
  DEFINITION_OF_DONE_ITEMS,
  DEFINITION_OF_DONE_SUMMARY,
  type DoDStatus,
} from "@/lib/definition-of-done-data";
import { Card, SectionHeading, Table } from "@/components/ui";

const STATUS_STYLE: Record<DoDStatus, string> = {
  DONE: "bg-[#16A34A]/10 text-[#16A34A] border-[#16A34A]/30",
  PARTIAL: "bg-[#F59E0B]/10 text-[#B45309] border-[#F59E0B]/40",
  "NOT DONE": "bg-[#DC2626]/10 text-[#DC2626] border-[#DC2626]/30",
};

function StatusBadge({ status }: { status: DoDStatus }) {
  return (
    <span
      className={`inline-flex items-center rounded-[6px] border px-2 py-0.5 text-xs font-semibold uppercase tracking-wide ${STATUS_STYLE[status]}`}
    >
      {status}
    </span>
  );
}

export default function DataIntegrityPage({
  params,
}: {
  params: { ticker: string };
}) {
  const doneCount = DEFINITION_OF_DONE_ITEMS.filter((i) => i.status === "DONE").length;
  const total = DEFINITION_OF_DONE_ITEMS.length;

  return (
    <div className="space-y-6">
      <SectionHeading
        title="Data Integrity & Definition of Done"
        description={`Honest, item-by-item status against the master spec's Definition of Done · ${doneCount}/${total} DONE`}
      />

      <Card>
        <p className="text-sm text-[#111827]/70">{DEFINITION_OF_DONE_SUMMARY}</p>
      </Card>

      <Card>
        <Table headers={["#", "Item", "Status", "Note"]}>
          {DEFINITION_OF_DONE_ITEMS.map((row) => (
            <tr key={row.number} className="border-b border-[#E5E7EB] last:border-0 align-top">
              <td className="px-4 py-2.5 tabular-nums text-[#111827]/40">{row.number}</td>
              <td className="px-4 py-2.5 font-medium text-[#111827] whitespace-nowrap">
                {row.item}
              </td>
              <td className="px-4 py-2.5">
                <StatusBadge status={row.status} />
              </td>
              <td className="px-4 py-2.5 text-xs text-[#111827]/70">{row.note}</td>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}
