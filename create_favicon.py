"""
Script to create a favicon.ico with dual sync arrows
"""
from PIL import Image, ImageDraw
import math
import os

def create_sync_favicon():
    """Create a favicon with dual sync arrows"""
    # Create multiple sizes for ICO format (16x16, 32x32, 48x48)
    sizes = [16, 32, 48]
    images = []
    
    for size in sizes:
        # Create image with transparent background
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # Calculate center and radius
        center = size // 2
        radius = size * 0.35
        
        # Arrow properties
        arrow_width = max(2, size // 8)
        arrow_head_size = max(3, size // 6)
        
        # Draw two curved arrows in a circular sync pattern
        # Arrow 1: Top-right to bottom-left (clockwise)
        # Arrow 2: Bottom-left to top-right (counter-clockwise)
        
        # Define the arc angles
        start_angle1 = -45  # Top-right
        end_angle1 = 135    # Bottom-left
        start_angle2 = 135  # Bottom-left
        end_angle2 = 315    # Top-right (wrapping)
        
        # Draw first arrow (top-right to bottom-left, clockwise)
        points1 = []
        for angle in range(int(start_angle1), int(end_angle1) + 1, 2):
            rad = math.radians(angle)
            x = center + radius * math.cos(rad)
            y = center + radius * math.sin(rad)
            points1.append((x, y))
        
        if len(points1) > 1:
            # Draw the arc line
            for i in range(len(points1) - 1):
                draw.line([points1[i], points1[i+1]], fill=(0, 123, 255, 255), width=arrow_width)
            
            # Draw arrowhead at the end
            end_rad = math.radians(end_angle1)
            arrow_x = center + radius * math.cos(end_rad)
            arrow_y = center + radius * math.sin(end_rad)
            
            # Arrowhead direction
            arrow_dir = math.radians(end_angle1 + 90)
            arrow_head_x1 = arrow_x + arrow_head_size * math.cos(arrow_dir + 0.5)
            arrow_head_y1 = arrow_y + arrow_head_size * math.sin(arrow_dir + 0.5)
            arrow_head_x2 = arrow_x + arrow_head_size * math.cos(arrow_dir - 0.5)
            arrow_head_y2 = arrow_y + arrow_head_size * math.sin(arrow_dir - 0.5)
            
            draw.polygon([(arrow_x, arrow_y), (arrow_head_x1, arrow_head_y1), (arrow_head_x2, arrow_head_y2)], 
                        fill=(0, 123, 255, 255))
        
        # Draw second arrow (bottom-left to top-right, counter-clockwise)
        points2 = []
        for angle in range(int(start_angle2), int(end_angle2) + 1, 2):
            rad = math.radians(angle)
            x = center + radius * math.cos(rad)
            y = center + radius * math.sin(rad)
            points2.append((x, y))
        
        if len(points2) > 1:
            # Draw the arc line
            for i in range(len(points2) - 1):
                draw.line([points2[i], points2[i+1]], fill=(40, 167, 69, 255), width=arrow_width)
            
            # Draw arrowhead at the end
            end_rad = math.radians(end_angle2)
            arrow_x = center + radius * math.cos(end_rad)
            arrow_y = center + radius * math.sin(end_rad)
            
            # Arrowhead direction
            arrow_dir = math.radians(end_angle2 - 90)
            arrow_head_x1 = arrow_x + arrow_head_size * math.cos(arrow_dir + 0.5)
            arrow_head_y1 = arrow_y + arrow_head_size * math.sin(arrow_dir + 0.5)
            arrow_head_x2 = arrow_x + arrow_head_size * math.cos(arrow_dir - 0.5)
            arrow_head_y2 = arrow_y + arrow_head_size * math.sin(arrow_dir - 0.5)
            
            draw.polygon([(arrow_x, arrow_y), (arrow_head_x1, arrow_head_y1), (arrow_head_x2, arrow_head_y2)], 
                        fill=(40, 167, 69, 255))
        
        images.append(img)
    
    # Create static directory if it doesn't exist
    static_dir = 'static'
    if not os.path.exists(static_dir):
        os.makedirs(static_dir)
    
    # Save as ICO file
    ico_path = os.path.join(static_dir, 'favicon.ico')
    images[0].save(ico_path, format='ICO', sizes=[(s, s) for s in sizes])
    print(f"Favicon created successfully at {ico_path}")
    return ico_path

if __name__ == '__main__':
    create_sync_favicon()

