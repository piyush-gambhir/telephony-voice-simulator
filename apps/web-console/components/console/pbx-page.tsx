"use client";

import { useState } from "react";

import { EmptyStrip, PageFrame, PageHeader, Segmented } from "@/components/console/kit";
import { ErrorLine } from "@/components/console/models-timeline";
import { PbxCapacity } from "@/components/console/pbx-capacity";
import { PbxCases } from "@/components/console/pbx-cases";
import { PbxMap } from "@/components/console/pbx-map";
import { PbxRoute } from "@/components/console/pbx-route";
import { useApi } from "@/hooks/use-api";
import { pbx } from "@/lib/workflows-api";

type Section = "map" | "route" | "cases" | "capacity";

export function PbxPage() {
  const [section, setSection] = useState<Section>("map");
  const map = useApi((signal) => pbx.map(signal), []);

  return (
    <PageFrame>
      <PageHeader title="Dealer PBX" count={map.data?.group} freshness={false}>
        <Segmented<Section>
          label="Dealer PBX section"
          value={section}
          onChange={setSection}
          options={[
            { id: "map", label: "Map" },
            { id: "route", label: "Route a call" },
            { id: "cases", label: "Cases" },
            { id: "capacity", label: "Capacity" },
          ]}
        />
      </PageHeader>

      {section === "cases" ? (
        <PbxCases />
      ) : map.error ? (
        <ErrorLine message={map.error} />
      ) : !map.data ? (
        <EmptyStrip title="Loading the dealer group." />
      ) : section === "map" ? (
        <PbxMap map={map.data} />
      ) : section === "route" ? (
        <PbxRoute map={map.data} />
      ) : (
        <PbxCapacity map={map.data} />
      )}
    </PageFrame>
  );
}
