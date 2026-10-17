// Web Bluetooth transport: the phone connects to the backend host, which
// advertises as a BLE peripheral ("Motion Hub"), and writes samples into a
// characteristic. Android Chrome only - iOS Safari has no Web Bluetooth.
// Frame layouts must match backend/app/ble_server.py.

export const SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb";
const DATA_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb";
const HELLO_UUID = "0000ffe2-0000-1000-8000-00805f9b34fb";

export const bleSupported = () => typeof navigator !== "undefined" && !!navigator.bluetooth;

// FNV-1a: stable 32-bit hash of the persistent device id.
export function hash32(str) {
  let h = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h >>> 0;
}

// Must be called from a user gesture (the Start tap) - requestDevice shows the
// system chooser. Returns {send(x,y,z), close()}.
export async function connectBle({ deviceId, name, onStatus }) {
  onStatus?.("connecting");
  const device = await navigator.bluetooth.requestDevice({
    filters: [{ services: [SERVICE_UUID] }],
  });
  const server = await device.gatt.connect();
  const service = await server.getPrimaryService(SERVICE_UUID);
  const data = await service.getCharacteristic(DATA_UUID);
  const hello = await service.getCharacteristic(HELLO_UUID);

  const id = hash32(deviceId);

  // hello: uint32 id + utf-8 name, capped at 20 bytes for the default ATT MTU
  const nameBytes = new TextEncoder().encode(name).slice(0, 16);
  const hb = new Uint8Array(4 + nameBytes.length);
  new DataView(hb.buffer).setUint32(0, id, true);
  hb.set(nameBytes, 4);
  await hello.writeValue(hb);

  let closed = false;
  device.addEventListener("gattserverdisconnected", () => {
    if (!closed) onStatus?.("closed");
  });
  onStatus?.("open");

  const frame = new ArrayBuffer(16);
  const view = new DataView(frame);
  view.setUint32(0, id, true);
  let busy = false;
  const write = data.writeValueWithoutResponse
    ? (b) => data.writeValueWithoutResponse(b)
    : (b) => data.writeValue(b);

  return {
    // Drops a sample if the previous write is still in flight rather than queueing.
    send(x, y, z) {
      if (busy || !device.gatt.connected) return;
      view.setFloat32(4, x, true);
      view.setFloat32(8, y, true);
      view.setFloat32(12, z, true);
      busy = true;
      write(frame).catch(() => {}).finally(() => (busy = false));
    },
    close() {
      closed = true;
      if (device.gatt.connected) device.gatt.disconnect();
    },
  };
}
