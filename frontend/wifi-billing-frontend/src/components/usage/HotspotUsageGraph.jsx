import { useEffect, useState } from "react";
import UsageGraph from "./UsageGraph";
import { fetchHotspotUsageDaily } from "../../services/hotspot";

export default function HotspotUsageGraph() {
  const [data, setData] = useState([]);

  useEffect(() => {
    fetchHotspotUsageDaily(7).then(setData);
  }, []);

  return (
    <UsageGraph
      title="Hotspot Usage (Last 7 Days)"
      data={data}
    />
  );
}
