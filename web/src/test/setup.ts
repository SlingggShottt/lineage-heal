import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});

// React Flow measures and observes the page; jsdom has neither, so stand-ins are provided.
class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
Object.defineProperty(globalThis, "ResizeObserver", {
  value: ResizeObserverStub,
  configurable: true,
});

class DOMMatrixReadOnlyStub {
  m22 = 1;
  constructor(transform?: string) {
    const scale = /scale\(([\d.]+)\)/.exec(transform ?? "");
    this.m22 = scale ? Number(scale[1]) : 1;
  }
}
Object.defineProperty(globalThis, "DOMMatrixReadOnly", {
  value: DOMMatrixReadOnlyStub,
  configurable: true,
});

// jsdom has no modal <dialog>; these stand-ins open and close it the way a browser does, closed
// content staying out of the accessibility tree.
Object.defineProperty(HTMLDialogElement.prototype, "showModal", {
  value(this: HTMLDialogElement) {
    this.setAttribute("open", "");
  },
  configurable: true,
});
Object.defineProperty(HTMLDialogElement.prototype, "close", {
  value(this: HTMLDialogElement) {
    this.removeAttribute("open");
    this.dispatchEvent(new Event("close"));
  },
  configurable: true,
});
