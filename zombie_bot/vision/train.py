"""Teacher -> student distillation, step 2: train the on-device yolo11n student, export TensorRT FP16.

python -m vision.train --data D:/czb/datasets/zombies/data.yaml --epochs 60
Prints the engine path; set it as vision.weights in config.yaml (with classes: null).
"""
import argparse
import os
import shutil


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="D:/czb/datasets/zombies/data.yaml")
    ap.add_argument("--base", default="D:/czb/models/yolo11n.pt", help="COCO-pretrained start point")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--batch", type=int, default=24)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--name", default="zombies_v1")
    ap.add_argument("--out", default="D:/czb/models")
    args = ap.parse_args()

    from ultralytics import YOLO
    model = YOLO(args.base)
    model.train(data=args.data, epochs=args.epochs, imgsz=args.imgsz, batch=args.batch, device=0,
                project="D:/czb/runs", name=args.name, exist_ok=True, patience=20, cache="ram",
                workers=4, cos_lr=True, close_mosaic=10, plots=True,
                # game footage: lighting varies a lot, horizontal flips are fine, no vertical flips
                hsv_v=0.5, fliplr=0.5, flipud=0.0, scale=0.5, degrees=0.0)
    best = f"D:/czb/runs/{args.name}/weights/best.pt"
    dst = os.path.join(args.out, f"{args.name}.pt")
    shutil.copy(best, dst)
    metrics = YOLO(dst).val(data=args.data, imgsz=args.imgsz, device=0, verbose=False)
    print(f"[train] val mAP50={metrics.box.map50:.3f} mAP50-95={metrics.box.map:.3f} "
          f"P={metrics.box.mp:.3f} R={metrics.box.mr:.3f}")
    engine = YOLO(dst).export(format="engine", imgsz=args.imgsz, half=True, workspace=2, verbose=False)
    print(f"[train] student weights: {dst}\n[train] TensorRT engine: {engine}")


if __name__ == "__main__":
    main()
