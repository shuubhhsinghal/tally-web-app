'use client';
import { useState, useEffect, useRef } from "react";
import { X, Check, RefreshCcw } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { useUI } from "@/context/UIContext";

export function CameraCapture({ onCapture, onClose }) {
  const { showToast } = useUI();
  const [mode, setMode] = useState("camera"); // 'camera' or 'crop'
  const [stream, setStream] = useState(null);
  const [photoBlob, setPhotoBlob] = useState(null);
  const [photoUrl, setPhotoUrl] = useState(null);
  const [imgDim, setImgDim] = useState({ w: 0, h: 0 });
  const [isVideoPlaying, setIsVideoPlaying] = useState(false);
  
  const videoRef = useRef(null);
  const containerRef = useRef(null);
  
  // Points stored as fractions (0.0 to 1.0) of the image width/height
  const [points, setPoints] = useState([
    { x: 0.1, y: 0.1 }, // TL
    { x: 0.9, y: 0.1 }, // TR
    { x: 0.9, y: 0.9 }, // BR
    { x: 0.1, y: 0.9 }  // BL
  ]);

  const [activePointIdx, setActivePointIdx] = useState(null);

  // Stop all camera tracks
  const stopCamera = () => {
    if (stream) {
      stream.getTracks().forEach(track => track.stop());
      setStream(null);
    }
  };

  useEffect(() => {
    // Start camera when mode is 'camera'
    if (mode === "camera") {
      startCamera();
    }
    return () => {
      stopCamera();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);

  useEffect(() => {
    return () => {
      stopCamera();
      if (photoUrl) URL.revokeObjectURL(photoUrl);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const startCamera = async () => {
    let mediaStream = null;
    let track = null;
    
    // Progressive fallbacks for Android compatibility
    const constraintsList = [
      // 1. Ideal high-res environment
      { video: { facingMode: { ideal: "environment" }, width: { ideal: 4096 }, height: { ideal: 2160 } }, audio: false },
      // 2. Simple environment
      { video: { facingMode: { ideal: "environment" } }, audio: false },
      // 3. Any camera
      { video: true, audio: false }
    ];

    for (let i = 0; i < constraintsList.length; i++) {
      try {
        console.log(`[Camera] Trying constraints fallback level ${i}:`, constraintsList[i]);
        mediaStream = await navigator.mediaDevices.getUserMedia(constraintsList[i]);
        track = mediaStream.getVideoTracks()[0];
        console.log(`[Camera] Success at level ${i}. Track settings:`, track.getSettings());
        if (track.getCapabilities) {
           console.log(`[Camera] Track capabilities:`, track.getCapabilities());
        }
        break;
      } catch (err) {
        console.warn(`[Camera] Failed at level ${i}:`, err);
      }
    }

    if (!mediaStream) {
      console.error("[Camera] All getUserMedia constraints failed");
      showToast("Camera access denied or unavailable. Please choose from photos.", "error");
      onClose();
      return;
    }

    console.log(`[Camera] Stream active: ${mediaStream.active}, Tracks: ${mediaStream.getVideoTracks().length}`);
    console.log(`[Camera] Track readyState: ${track.readyState}, enabled: ${track.enabled}, muted: ${track.muted}`);

    setStream(mediaStream);
    
    if (videoRef.current) {
      const video = videoRef.current;
      video.srcObject = mediaStream;
      console.log(`[Camera] srcObject assigned. video.readyState = ${video.readyState}`);
      
      try {
        await video.play();
        console.log("[Camera] video.play() promise resolved successfully");
      } catch (e) {
        console.error("[Camera] video.play() rejected:", e);
      }
    }
  };

  const handleCapture = async () => {
    if (!videoRef.current || !stream) return;
    const track = stream.getVideoTracks()[0];
    
    // Attempt high-res ImageCapture API first (Supported on Chrome Android, etc)
    if ('ImageCapture' in window) {
      try {
        const imageCapture = new ImageCapture(track);
        const blob = await imageCapture.takePhoto();
        console.log("Captured via ImageCapture API. Blob size:", blob.size);
        
        const url = URL.createObjectURL(blob);
        const img = new Image();
        img.onload = () => {
          console.log("ImageCapture dimensions:", img.naturalWidth, "x", img.naturalHeight);
          setPhotoBlob(blob);
          setPhotoUrl(url);
          setImgDim({ w: img.naturalWidth, h: img.naturalHeight });
          stopCamera();
          setMode("crop");
          setPoints([{ x: 0.1, y: 0.1 }, { x: 0.9, y: 0.1 }, { x: 0.9, y: 0.9 }, { x: 0.1, y: 0.9 }]);
        };
        img.onerror = () => {
          console.warn("Failed to load ImageCapture blob");
          fallbackCanvasCapture();
        };
        img.src = url;
        return;
      } catch (err) {
        console.warn("ImageCapture failed or not supported, falling back to canvas", err);
      }
    }
    
    fallbackCanvasCapture();
  };

  const fallbackCanvasCapture = () => {
    const video = videoRef.current;
    if (!video) return;
    
    console.log("Fallback capture via canvas. Video dimensions:", video.videoWidth, "x", video.videoHeight);
    
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
      console.log("Captured via Canvas Fallback. Blob size:", blob.size);
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
    setMode("camera");
  };

  const handleUsePhoto = () => {
    // Calculate bounding box of the 4 points in original image coordinates
    let minX = 1, minY = 1, maxX = 0, maxY = 0;
    points.forEach(p => {
      if (p.x < minX) minX = p.x;
      if (p.x > maxX) maxX = p.x;
      if (p.y < minY) minY = p.y;
      if (p.y > maxY) maxY = p.y;
    });

    // Clamp to [0, 1]
    minX = Math.max(0, minX);
    minY = Math.max(0, minY);
    maxX = Math.min(1, maxX);
    maxY = Math.min(1, maxY);

    // Pixel coordinates mapped to original resolution
    const sx = Math.floor(minX * imgDim.w);
    const sy = Math.floor(minY * imgDim.h);
    const sWidth = Math.floor((maxX - minX) * imgDim.w);
    const sHeight = Math.floor((maxY - minY) * imgDim.h);

    if (sWidth <= 0 || sHeight <= 0) {
      showToast("Invalid crop area", "error");
      return;
    }

    // Load original image to crop
    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = sWidth;
      canvas.height = sHeight;
      const ctx = canvas.getContext("2d");
      
      ctx.drawImage(img, sx, sy, sWidth, sHeight, 0, 0, sWidth, sHeight);
      
      canvas.toBlob((blob) => {
        if (!blob) {
          showToast("Failed to crop image.", "error");
          return;
        }
        const file = new File([blob], `invoice-capture-${Date.now()}.jpg`, { type: "image/jpeg" });
        onCapture(file); // Parent handles closing
      }, "image/jpeg", 0.95);
    };
    img.onerror = () => {
      showToast("Failed to process captured image", "error");
    };
    img.src = photoUrl;
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
            <Button className="bg-teal-500 hover:bg-teal-600 text-white h-12 px-6 rounded-full" onClick={handleUsePhoto}>
              <Check className="w-5 h-5 mr-2" />
              Use Photo
            </Button>
          </>
        )}
      </div>
      
    </div>
  );
}
