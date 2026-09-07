"use client";

import { Download, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

function exportJson(records: unknown[], name: string) {
  const blob = new Blob([JSON.stringify(records, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${name}-${new Date().toISOString().slice(0, 10)}.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function HistoryToolbar({
  label, query, onQueryChange, status, statuses, onStatusChange,
  provider, providers, onProviderChange, records, total,
}: {
  label: string;
  query: string;
  onQueryChange: (value: string) => void;
  status: string;
  statuses: string[];
  onStatusChange: (value: string) => void;
  provider: string;
  providers: string[];
  onProviderChange: (value: string) => void;
  records: unknown[];
  total: number;
}) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-3">
        <div className="relative min-w-48 flex-1">
          <Search className="absolute left-3 top-3 size-4 text-muted-foreground" />
          <Input
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            placeholder={`Search ${label}, IDs, scenarios, or numbers`}
            aria-label={`Search ${label}`}
            className="pl-9"
          />
        </div>
        <Select value={status} onValueChange={onStatusChange}>
          <SelectTrigger aria-label={`Filter ${label} by status`} className="w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All statuses</SelectItem>
            {statuses.map((value) => (
              <SelectItem key={value} value={value}>{value.replaceAll("_", " ")}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={provider} onValueChange={onProviderChange}>
          <SelectTrigger aria-label={`Filter ${label} by provider`} className="w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All providers</SelectItem>
            {providers.map((value) => <SelectItem key={value} value={value}>{value}</SelectItem>)}
          </SelectContent>
        </Select>
        <Button variant="outline" disabled={!records.length} onClick={() => exportJson(records, label)}>
          <Download /> Export JSON
        </Button>
      </div>
      <p className="text-xs text-muted-foreground" aria-live="polite">
        {records.length} of {total} recent {label} loaded. Search and export apply to these records.
      </p>
    </div>
  );
}

export function HistoryPagination({ page, count, pageSize, onPageChange }: {
  page: number;
  count: number;
  pageSize: number;
  onPageChange: (page: number) => void;
}) {
  if (count <= pageSize) return null;
  const pages = Math.ceil(count / pageSize);
  return (
    <div className="flex items-center justify-end gap-3">
      <Button variant="outline" disabled={page === 0} onClick={() => onPageChange(page - 1)}>Previous</Button>
      <span className="text-sm text-muted-foreground">Page {page + 1} of {pages}</span>
      <Button variant="outline" disabled={page + 1 >= pages} onClick={() => onPageChange(page + 1)}>Next</Button>
    </div>
  );
}
