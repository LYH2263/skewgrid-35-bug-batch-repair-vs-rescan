import { FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getJSON, sendJSON, type Layer } from "../api";

export default function Layers() {
  const [layers, setLayers] = useState<Layer[]>([]);
  const [err, setErr] = useState("");

  function load() {
    getJSON<Layer[]>("/api/layers").then(setLayers).catch((e) => setErr(String(e)));
  }

  useEffect(() => {
    load();
  }, []);

  async function onCreate(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    setErr("");
    try {
      await sendJSON("/api/layers", "POST", {
        slug: fd.get("slug"),
        name: fd.get("name"),
        description: fd.get("description"),
        max_z: Number(fd.get("max_z") || 3),
      });
      load();
      e.currentTarget.reset();
    } catch (ex) {
      setErr(String(ex));
    }
  }

  return (
    <div className="page">
      <h1>图层</h1>
      {err && <p className="err">{err}</p>}
      <table>
        <thead>
          <tr>
            <th>名称</th>
            <th>slug</th>
            <th>provider</th>
            <th>z</th>
            <th>入口</th>
          </tr>
        </thead>
        <tbody>
          {layers.map((l) => (
            <tr key={l.slug}>
              <td>{l.name}</td>
              <td className="mono">{l.slug}</td>
              <td>{l.provider}</td>
              <td>
                {l.min_z}–{l.max_z}
              </td>
              <td className="row">
                <Link to={`/layers/${l.slug}/map`}>地图</Link>
                <Link to={`/layers/${l.slug}/coverage`}>覆盖率</Link>
                <Link to={`/layers/${l.slug}/upstream`}>上游</Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <h2>新建图层</h2>
      <p className="lead">只写元数据，瓦片要去「任务」里跑 prerender，或等 0-1 接上游。</p>
      <form className="form" onSubmit={onCreate}>
        <input name="slug" placeholder="slug，如 city-east" required />
        <input name="name" placeholder="显示名" required />
        <input name="description" placeholder="说明" />
        <input name="max_z" type="number" defaultValue={3} />
        <button type="submit">创建</button>
      </form>
    </div>
  );
}
