'use client';
import { useState, useEffect, useRef } from "react";
import { X, Check, RefreshCcw } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { useUI } from "@/context/UIContext";

export function CameraCapture({ onCapture, onClose, initialPhotoFile, onRetake }) {
  const { showToast } = useUI();
  const [mode, setMode] = useState(initialPhotoFile ? "crop" : "camera"); // 'camera' or 'crop'
  const [photoBlob, setPhotoBlob] = useState(null);
  const [photoUrl, setPhotoUrl] = useState(null);
  const [imgDim, setImgDim] = useState({ w: 0, h: 0 });
  const [isVideoPlaying, setIsVideoPlaying] = useState(false);
  
  const videoRef = useRef(null);
  const containerRef = useRef(null);
  const streamRef = useRef(null);
  const isInitializingRef = useRef(false);
  const sessionRef = useRef(Math.floor(Math.random() * 1000000));
  
  // Points stored as fractions (0.0 to 1.0) of the image width/height
  const [points, setPoints] = useState([
    { x: 0.1, y: 0.1 }, // TL
    { x: 0.9, y: 0.1 }, // TR
    { x: 0.9, y: 0.9 }, // BR
    { x: 0.1, y: 0.9 }  // BL
  ]);

  const [activePointIdx, setActivePointIdx] = useState(null);
  const hasUserAdjustedRef = useRef(false);
  const [hasUserAdjusted, setHasUserAdjusted] = useState(false);
  const [isDetectingCorners, setIsDetectingCorners] = useState(false);

  // Ask Gemini for a starting guess at the document's corners as soon as a
  // photo is ready to crop -- purely a convenience: if it fails, times out,
  // or the user has already started dragging a handle, the default box
  // (or whatever the user has adjusted) is left alone. While this is in
  // flight, "Use Photo" is briefly disabled so a rushed tap can't submit the
  // generic default box before the real edges have a chance to load.
  useEffect(() => {
    if (mode !== "crop" || !photoBlob) return;
    hasUserAdjustedRef.current = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setHasUserAdjusted(false);
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setIsDetectingCorners(true);
    let cancelled = false;

    // Safety timeout: never block the user for more than a couple seconds,
    // even if detection is slow or fails silently.
    const timeoutId = setTimeout(() => {
      if (!cancelled) setIsDetectingCorners(false);
    }, 2500);

    (async () => {
      try {
        const fd = new FormData();
        fd.append("file", photoBlob, "photo.jpg");
        const res = await fetch("/api/purchase-item/detect-corners", { method: "POST", body: fd });
        if (!res.ok || cancelled) return;
        const data = await res.json();
        if (!cancelled && data.corners && !hasUserAdjustedRef.current) {
          setPoints(data.corners);
        }
      } catch (e) {
        // Silent -- detection is a nice-to-have, never blocks the crop screen
      } finally {
        if (!cancelled) setIsDetectingCorners(false);
      }
    })();

    return () => { cancelled = true; clearTimeout(timeoutId); };
  }, [photoBlob, mode]);

  // Stop all camera tracks
  const stopCamera = () => {
    console.log(`[CAMERA SESSION ${sessionRef.current}] stopCamera called. Stream exists?`, !!streamRef.current);
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(track => {
        console.log(`[CAMERA SESSION ${sessionRef.current}] track stop:`, track.label);
        track.stop();
      });
      streamRef.current = null;
    }
  };

  useEffect(() => {
    console.log(`[CAMERA SESSION ${sessionRef.current}] Component Mounted or Mode Changed. mode:`, mode);
    // Start camera when mode is 'camera'
    if (mode === "camera") {
      startCamera();
    }
    return () => {
      console.log(`[CAMERA SESSION ${sessionRef.current}] Cleanup running for mode:`, mode);
      stopCamera();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);

  useEffect(() => {
    if (initialPhotoFile) {
      console.log(`[CAMERA SESSION ${sessionRef.current}] Initializing directly into crop mode with native file:`, initialPhotoFile.name);
      const url = URL.createObjectURL(initialPhotoFile);
      const img = new Image();
      img.onload = () => {
        console.log(`[CAMERA SESSION ${sessionRef.current}] Native image dimensions: ${img.naturalWidth} x ${img.naturalHeight}`);
        setPhotoBlob(initialPhotoFile);
        setPhotoUrl(url);
        setImgDim({ w: img.naturalWidth, h: img.naturalHeight });
        setPoints([{ x: 0.1, y: 0.1 }, { x: 0.9, y: 0.1 }, { x: 0.9, y: 0.9 }, { x: 0.1, y: 0.9 }]);
      };
      img.onerror = () => {
        showToast("Failed to load native image for cropping", "error");
        onClose();
      };
      img.src = url;
    }
    
    return () => {
      stopCamera();
      if (photoUrl) URL.revokeObjectURL(photoUrl);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialPhotoFile]);

  const startCamera = async () => {
    if (isInitializingRef.current || streamRef.current) {
      console.log(`[CAMERA SESSION ${sessionRef.current}] startCamera ignored. isInitializing: ${isInitializingRef.current}, streamRef exists: ${!!streamRef.current}`);
      return;
    }
    isInitializingRef.current = true;
    
    let mediaStream = null;
    let track = null;
    
    // Diagnostic simple constraints to fix Android flicker
    const constraints = { video: { facingMode: { ideal: "environment" } }, audio: false };

    try {
      console.log(`[CAMERA SESSION ${sessionRef.current}] getUserMedia START. Constraints:`, constraints);
      mediaStream = await navigator.mediaDevices.getUserMedia(constraints);
      track = mediaStream.getVideoTracks()[0];
      console.log(`[CAMERA SESSION ${sessionRef.current}] getUserMedia SUCCESS. Track settings:`, track.getSettings());
    } catch (err) {
      console.warn(`[CAMERA SESSION ${sessionRef.current}] getUserMedia REJECTED:`, err);
    }

    if (!mediaStream) {
      console.error(`[CAMERA SESSION ${sessionRef.current}] Camera failed`);
      showToast("Camera access denied or unavailable. Please choose from photos.", "error");
      onClose();
      isInitializingRef.current = false;
      return;
    }

    streamRef.current = mediaStream;
    
    if (videoRef.current) {
      const video = videoRef.current;
      if (video.srcObject !== mediaStream) {
        video.srcObject = mediaStream;
        console.log(`[CAMERA SESSION ${sessionRef.current}] srcObject ASSIGNED.`);
      }
      
      try {
        console.log(`[CAMERA SESSION ${sessionRef.current}] video.play START`);
        await video.play();
        console.log(`[CAMERA SESSION ${sessionRef.current}] video.play SUCCESS`);
      } catch (e) {
        console.error(`[CAMERA SESSION ${sessionRef.current}] video.play REJECTED:`, e);
      }
    }
    
    isInitializingRef.current = false;
  };

  const handleCapture = async () => {
    if (!videoRef.current || !streamRef.current) return;
    const track = streamRef.current.getVideoTracks()[0];
    
    // Attempt high-res ImageCapture API first (Supported on Chrome Android, etc)
    if ('ImageCapture' in window) {
      try {
        console.log(`[CAMERA SESSION ${sessionRef.current}] ImageCapture API exists. Initializing.`);
        const imageCapture = new ImageCapture(track);
        
        let photoSettings = {};
        if (typeof imageCapture.getPhotoCapabilities === 'function') {
          try {
            const caps = await imageCapture.getPhotoCapabilities();
            console.log(`[CAMERA SESSION ${sessionRef.current}] Photo Capabilities:`, caps);
            if (caps.imageWidth && caps.imageWidth.max) {
              photoSettings.imageWidth = caps.imageWidth.max;
            }
            if (caps.imageHeight && caps.imageHeight.max) {
              photoSettings.imageHeight = caps.imageHeight.max;
            }
          } catch (capErr) {
            console.warn(`[CAMERA SESSION ${sessionRef.current}] getPhotoCapabilities failed:`, capErr);
          }
        }

        console.log(`[CAMERA SESSION ${sessionRef.current}] Calling takePhoto with settings:`, photoSettings);
        const blob = await imageCapture.takePhoto(photoSettings);
        console.log(`[CAMERA SESSION ${sessionRef.current}] takePhoto SUCCESS. Blob size:`, blob.size);
        
        const url = URL.createObjectURL(blob);
        const img = new Image();
        img.onload = () => {
          console.log(`[CAMERA SESSION ${sessionRef.current}] ImageCapture dimensions: ${img.naturalWidth} x ${img.naturalHeight}`);
          setPhotoBlob(blob);
          setPhotoUrl(url);
          setImgDim({ w: img.naturalWidth, h: img.naturalHeight });
          stopCamera();
          setMode("crop");
          setPoints([{ x: 0.1, y: 0.1 }, { x: 0.9, y: 0.1 }, { x: 0.9, y: 0.9 }, { x: 0.1, y: 0.9 }]);
        };
        img.onerror = () => {
          console.warn(`[CAMERA SESSION ${sessionRef.current}] Failed to load ImageCapture blob`);
          fallbackCanvasCapture();
        };
        img.src = url;
        return;
      } catch (err) {
        console.warn(`[CAMERA SESSION ${sessionRef.current}] ImageCapture takePhoto failed or not supported, falling back to canvas:`, err);
      }
    } else {
      console.log(`[CAMERA SESSION ${sessionRef.current}] ImageCapture not in window. Proceeding to fallback.`);
    }
    
    fallbackCanvasCapture();
  };

  const fallbackCanvasCapture = () => {
    const video = videoRef.current;
    if (!video) return;
    
    console.log(`[CAMERA SESSION ${sessionRef.current}] Fallback capture via canvas. Video dimensions: ${video.videoWidth} x ${video.videoHeight}`);
    
    // Create full resolution canvas
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    
    // Draw current frame
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    
    canvas.toBlob((blob) => {
      if (!blob) {
        showToast("Failed to capture image.", "error");
        return;
      }
      console.log(`[CAMERA SESSION ${sessionRef.current}] Captured via Canvas Fallback. Blob size:`, blob.size);
      const url = URL.createObjectURL(blob);
      setPhotoBlob(blob);
      setPhotoUrl(url);
      setImgDim({ w: canvas.width, h: canvas.height });
      stopCamera();
      setMode("crop");
      setPoints([{ x: 0.1, y: 0.1 }, { x: 0.9, y: 0.1 }, { x: 0.9, y: 0.9 }, { x: 0.1, y: 0.9 }]);
    }, "image/jpeg", 0.95);
  };

  const handleRetake = () => {
    if (photoUrl) URL.revokeObjectURL(photoUrl);
    setPhotoBlob(null);
    setPhotoUrl(null);
    
    if (initialPhotoFile && onRetake) {
      onRetake();
    } else {
      setMode("camera");
    }
  };

  const handleUsePhoto = () => {
    // No client-side cropping anymore -- the marked corners go to the backend
    // as-is, which runs a real perspective warp using the original photo.
    const finalFile = photoBlob instanceof File
      ? photoBlob
      : new File([photoBlob], `invoice-capture-${Date.now()}.jpg`, { type: "image/jpeg" });
    onCapture(finalFile, points); // Parent handles closing
  };

  // --- SVG Touch / Mouse Handlers ---
  
  const getEventCoords = (e) => {
    if (e.touches && e.touches.length > 0) {
      return { clientX: e.touches[0].clientX, clientY: e.touches[0].clientY };
    }
    return { clientX: e.clientX, clientY: e.clientY };
  };

  const handlePointerDown = (idx, e) => {
    e.preventDefault();
    hasUserAdjustedRef.current = true;
    setHasUserAdjusted(true);
    setActivePointIdx(idx);
  };

  const handlePointerMove = (e) => {
    if (activePointIdx === null || !containerRef.current) return;
    
    // We need the bounding rect of the *actual rendered image area* 
    // to map pointer coords to fractions.
    const rect = containerRef.current.getBoundingClientRect();
    
    const { clientX, clientY } = getEventCoords(e);
    let x = (clientX - rect.left) / rect.width;
    let y = (clientY - rect.top) / rect.height;
    
    // Clamp to 0..1
    x = Math.max(0, Math.min(1, x));
    y = Math.max(0, Math.min(1, y));

    setPoints(prev => {
      const next = [...prev];
      next[activePointIdx] = { x, y };
      return next;
    });
  };

  const handlePointerUp = () => {
    setActivePointIdx(null);
  };

  useEffect(() => {
    if (activePointIdx !== null) {
      window.addEventListener("mousemove", handlePointerMove);
      window.addEventListener("mouseup", handlePointerUp);
      window.addEventListener("touchmove", handlePointerMove, { passive: false });
      window.addEventListener("touchend", handlePointerUp);
      return () => {
        window.removeEventListener("mousemove", handlePointerMove);
        window.removeEventListener("mouseup", handlePointerUp);
        window.removeEventListener("touchmove", handlePointerMove);
        window.removeEventListener("touchend", handlePointerUp);
      };
    }
  }, [activePointIdx]);


  return (
    <div className="fixed inset-0 z-[100] bg-black flex flex-col overscroll-none touch-none">
      
      {/* Header */}
      <div className="w-full h-16 flex items-center justify-between px-4 shrink-0 bg-black z-50">
        <button onClick={onClose} className="p-2 text-white hover:bg-white/20 rounded-full">
          <X className="w-6 h-6" />
        </button>
        <span className="text-white font-medium">
          {mode === "camera" ? "Take Photo" : "Adjust Crop"}
        </span>
        <div className="w-10"></div> {/* Spacer for centering */}
      </div>

      {/* Main Content Area */}
      <div className="flex-1 w-full flex items-center justify-center relative overflow-hidden">
        
        {mode === "camera" && (
          <video 
            ref={videoRef}
            autoPlay 
            playsInline
            muted
            onLoadedMetadata={(e) => console.log(`[Video Event] loadedmetadata. Dimensions: ${e.target.videoWidth}x${e.target.videoHeight}`)}
            onCanPlay={(e) => console.log(`[Video Event] canplay. Dimensions: ${e.target.videoWidth}x${e.target.videoHeight}`)}
            onPlaying={(e) => {
              console.log(`[Video Event] playing. Dimensions: ${e.target.videoWidth}x${e.target.videoHeight}`);
              if (e.target.videoWidth > 0 && e.target.videoHeight > 0) {
                setIsVideoPlaying(true);
              } else {
                console.warn("[Video Event] playing but dimensions are 0!");
              }
            }}
            onWaiting={() => console.log("[Video Event] waiting")}
            onStalled={() => console.warn("[Video Event] stalled")}
            onError={(e) => console.error("[Video Event] error", e.target.error)}
            className="w-full h-full object-cover"
          />
        )}

        {mode === "crop" && photoUrl && (
          <div 
            className="relative"
            // We use a responsive container that matches the image aspect ratio
            style={{ 
              maxWidth: "100%", 
              maxHeight: "100%", 
              aspectRatio: `${imgDim.w} / ${imgDim.h}` 
            }}
            ref={containerRef}
          >
            <img 
              src={photoUrl} 
              alt="Captured" 
              className="w-full h-full object-contain pointer-events-none select-none block"
            />
            
            {/* Overlay SVG for drawing the polygon and handles */}
            <svg className="absolute inset-0 w-full h-full pointer-events-none" style={{ overflow: 'visible' }}>
              
              {/* Nested SVG to fix polygon percentage coordinates for the mask */}
              <svg viewBox="0 0 100 100" preserveAspectRatio="none" width="100%" height="100%" x="0" y="0">
                {/* Shaded area outside the polygon */}
                <mask id="crop-mask">
                  <rect width="100" height="100" fill="white" />
                  <polygon 
                    points={points.map(p => `${p.x * 100},${p.y * 100}`).join(" ")}
                    fill="black"
                  />
                </mask>
                <rect width="100" height="100" fill="rgba(0,0,0,0.5)" mask="url(#crop-mask)" />
              </svg>
              
              {/* Four connecting lines */}
              <line x1={`${points[0].x * 100}%`} y1={`${points[0].y * 100}%`} x2={`${points[1].x * 100}%`} y2={`${points[1].y * 100}%`} stroke="white" strokeWidth="2" style={{ filter: "drop-shadow(0px 0px 2px rgba(0,0,0,0.8))" }} />
              <line x1={`${points[1].x * 100}%`} y1={`${points[1].y * 100}%`} x2={`${points[2].x * 100}%`} y2={`${points[2].y * 100}%`} stroke="white" strokeWidth="2" style={{ filter: "drop-shadow(0px 0px 2px rgba(0,0,0,0.8))" }} />
              <line x1={`${points[2].x * 100}%`} y1={`${points[2].y * 100}%`} x2={`${points[3].x * 100}%`} y2={`${points[3].y * 100}%`} stroke="white" strokeWidth="2" style={{ filter: "drop-shadow(0px 0px 2px rgba(0,0,0,0.8))" }} />
              <line x1={`${points[3].x * 100}%`} y1={`${points[3].y * 100}%`} x2={`${points[0].x * 100}%`} y2={`${points[0].y * 100}%`} stroke="white" strokeWidth="2" style={{ filter: "drop-shadow(0px 0px 2px rgba(0,0,0,0.8))" }} />
              
              {/* Handles */}
              {points.map((p, idx) => (
                <g 
                  key={idx}
                  className="pointer-events-auto cursor-move touch-none"
                  onMouseDown={(e) => handlePointerDown(idx, e)}
                  onTouchStart={(e) => handlePointerDown(idx, e)}
                  // We just use SVG native positioning for handles
                >
                  {/* Invisible larger hit area for touch */}
                  <circle 
                    cx={`${p.x * 100}%`} cy={`${p.y * 100}%`} r="40" fill="transparent" 
                  />
                  {/* Visible handle */}
                  <circle 
                    cx={`${p.x * 100}%`} cy={`${p.y * 100}%`} r="6" fill="white" 
                    stroke="rgba(0,0,0,0.6)" strokeWidth="1" 
                    style={{ filter: "drop-shadow(0px 0px 4px rgba(0,0,0,0.8))" }}
                  />
                </g>
              ))}
            </svg>
          </div>
        )}
      </div>

      {/* Footer Controls */}
      <div className="h-[140px] w-full flex items-center justify-around px-6 bg-black pb-8 shrink-0">
        {mode === "camera" ? (
          <>
            <div className="w-16"></div>
            <button 
              onClick={handleCapture}
              disabled={!isVideoPlaying}
              className={`w-16 h-16 rounded-full border-4 flex items-center justify-center transition-colors ${isVideoPlaying ? 'border-white hover:bg-white/20 cursor-pointer' : 'border-gray-500 opacity-50 cursor-not-allowed'}`}
            >
              <div className={`w-12 h-12 rounded-full ${isVideoPlaying ? 'bg-white' : 'bg-gray-500'}`}></div>
            </button>
            <div className="w-16"></div>
          </>
        ) : (
          <>
            <Button variant="ghost" className="text-white hover:bg-white/20 h-12 px-6 rounded-full" onClick={handleRetake}>
              <RefreshCcw className="w-5 h-5 mr-2" />
              Retake
            </Button>
            <Button
              className="bg-teal-500 hover:bg-teal-600 text-white h-12 px-6 rounded-full"
              onClick={handleUsePhoto}
              disabled={isDetectingCorners && !hasUserAdjusted}
            >
              {isDetectingCorners && !hasUserAdjusted ? (
                <>
                  <span className="w-4 h-4 border-2 border-white/40 border-t-white rounded-full animate-spin mr-2" />
                  Finding edges...
                </>
              ) : (
                <>
                  <Check className="w-5 h-5 mr-2" />
                  Use Photo
                </>
              )}
            </Button>
          </>
        )}
      </div>
      
    </div>
  );
}
