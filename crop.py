import cv2
import os
import easyocr
import re

def crop_and_rename_batch(reference_image_path, input_folder, output_folder, max_window_width=1200, max_window_height=800):
    # 1. Ensure the output folder exists
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    # ==========================================
    # STEP 1: SET THE CROP SIZE (UI POPUP)
    # ==========================================
    print(f"Loading reference image: {reference_image_path}")
    ref_img = cv2.imread(reference_image_path)

    if ref_img is None:
        print(f"Error: Could not load the reference image at '{reference_image_path}'.")
        return

    # Scale image to fit screen for the popup
    orig_h, orig_w = ref_img.shape[:2]
    scale = 1.0
    if orig_w > max_window_width or orig_h > max_window_height:
        scale_w = max_window_width / orig_w
        scale_h = max_window_height / orig_h
        scale = min(scale_w, scale_h)

    new_w, new_h = int(orig_w * scale), int(orig_h * scale)
    display_img = cv2.resize(ref_img, (new_w, new_h))

    print("\n--- INSTRUCTIONS ---")
    print("1. Drag a box around the text/area you want to crop.")
    print("2. Press ENTER or SPACE to confirm.")
    print("3. Press 'c' to cancel.")

    roi = cv2.selectROI("Select Area to Crop & Read", display_img, fromCenter=False, showCrosshair=True)
    cv2.destroyAllWindows()

    x_disp, y_disp, w_disp, h_disp = roi
    if w_disp == 0 or h_disp == 0:
        print("Cancelled. No images processed.")
        return

    # Scale coordinates back to full resolution
    x_orig, y_orig = int(x_disp / scale), int(y_disp / scale)
    w_orig, h_orig = int(w_disp / scale), int(h_disp / scale)

    # ==========================================
    # STEP 2: LOAD THE AI MODEL
    # ==========================================
    valid_extensions = ('.jpg', '.jpeg', '.png')
    image_files = [f for f in os.listdir(input_folder) if f.lower().endswith(valid_extensions)]

    if not image_files:
        print(f"No images found in '{input_folder}'.")
        return

    print("\nLoading EasyOCR AI Model... (this may take a few seconds)")
    reader = easyocr.Reader(['en'])
    print("Model loaded! Starting batch process...\n")

    # ==========================================
    # STEP 3: BATCH CROP AND OCR
    # ==========================================
    success_count = 0
    
    for filename in image_files:
        input_path = os.path.join(input_folder, filename)
        img = cv2.imread(input_path)

        if img is not None:
            # A. Crop the image using the coordinates
            cropped_img = img[y_orig:y_orig+h_orig, x_orig:x_orig+w_orig]

            # B. Read the text directly from the newly cropped area
            # EasyOCR can read directly from OpenCV's image format!
            results = reader.readtext(cropped_img, detail=0)
            
            # C. Clean up the text for the filename
            if results:
                first_line = results[0]
                safe_name = re.sub(r'[\\/*?:"<>|]', "", first_line)
                safe_name = safe_name[:30].strip()
            else:
                safe_name = ""

            # D. Figure out what to name the file
            file_extension = os.path.splitext(filename)[1] 
            
            if safe_name:
                new_filename = safe_name + file_extension
            else:
                # If the AI couldn't read any text, just use the original name + "_cropped"
                base_name = os.path.splitext(filename)[0]
                new_filename = f"{base_name}_cropped{file_extension}"
                print(f"  [-] No text found in {filename}. Using default name.")

            new_filepath = os.path.join(output_folder, new_filename)

            # E. Check for duplicates (don't overwrite)
            counter = 1
            while os.path.exists(new_filepath):
                new_filename = f"{safe_name}_{counter}{file_extension}" if safe_name else f"{base_name}_cropped_{counter}{file_extension}"
                new_filepath = os.path.join(output_folder, new_filename)
                counter += 1

            # F. Save the cropped image
            cv2.imwrite(new_filepath, cropped_img)
            print(f"  [+] Saved: '{new_filename}'")
            success_count += 1
        else:
            print(f"  [-] Failed to open: {filename}")

    print(f"\nDone! Successfully processed {success_count} out of {len(image_files)} images.")

if __name__ == "__main__":
    # --- SETUP YOUR PATHS HERE ---
    
    # 1. The specific image you want to open FIRST to draw the box
    REFERENCE_IMAGE = 'QR_CODE/photo_2026-04-10_16-38-13.jpg' 
    
    # 2. The folder containing all the images you want to crop
    INPUT_DIR = 'QR_CODE' 
    
    # 3. The folder where the cropped/renamed results will go
    OUTPUT_DIR = 'QR_CODE_RESULTS' 
    
    crop_and_rename_batch(REFERENCE_IMAGE, INPUT_DIR, OUTPUT_DIR)