__author__ = 'Brian M Anderson'
# Created on 4/28/2020
import SimpleITK as sitk
import numpy as np
from _collections import OrderedDict
from .Plot_And_Scroll_Images.Plot_Scroll_Images import plot_scroll_Image, plt


def get_start_stop(annotation, extension=np.inf):
    non_zero_values = np.where(np.max(annotation,axis=(1,2)) > 0)[0]
    start, stop = -1, -1
    if non_zero_values.any():
        start = int(non_zero_values[0])
        stop = int(non_zero_values[-1])
        start = max([start - extension, 0])
        stop = min([stop + extension, annotation.shape[0]])
    return start, stop


def get_bounding_boxes(annotation_handle,value):
    Connected_Component_Filter = sitk.ConnectedComponentImageFilter()
    stats = sitk.LabelShapeStatisticsImageFilter()
    thresholded_image = sitk.BinaryThreshold(annotation_handle,lowerThreshold=value,upperThreshold=value+1)
    connected_image = Connected_Component_Filter.Execute(thresholded_image)
    stats.Execute(connected_image)
    bounding_boxes = [stats.GetBoundingBox(l) for l in stats.GetLabels()]
    volumes = np.asarray([stats.GetPhysicalSize(l) for l in stats.GetLabels()]).astype('float32')
    return bounding_boxes, volumes


class Image_Processor(object):
    def parse(self, input_features):
        return input_features


class Clip_Images_By_Extension(Image_Processor):
    def __init__(self, extension=np.inf):
        self.extension = extension

    def parse(self, input_features):
        image = input_features['image']
        annotation = input_features['annotation']
        start, stop = get_start_stop(annotation, self.extension)
        if start != -1 and stop != -1:
            image, annotation = image[start:stop, ...], annotation[start:stop, ...]
        input_features['image'] = image
        input_features['annotation'] = annotation
        return input_features


class Distribute_into_3D(Image_Processor):
    def __init__(self, max_z=np.inf, mirror_small_bits=True):
        self.max_z = max_z
        self.mirror_small_bits = mirror_small_bits

    def parse(self, input_features):
        out_features = {}
        start_chop = 0
        image_base = input_features['image']
        annotation_base = input_features['annotation']
        image_path = input_features['image_path']
        spacing = input_features['spacing']
        z_images_base, rows, cols = image_base.shape
        step = min([self.max_z, z_images_base])
        for index in range(z_images_base // step + 1):
            image_features = OrderedDict()
            if start_chop >= z_images_base:
                continue
            image = image_base[start_chop:start_chop + step, ...]
            annotation = annotation_base[start_chop:start_chop + step, ...]
            if image.shape[0] < step:
                if self.mirror_small_bits:
                    while image.shape[0] < step:
                        mirror_image = np.flip(image, axis=0)
                        mirror_annotation = np.flip(annotation, axis=0)
                        image = np.concatenate([image, mirror_image], axis=0)
                        annotation = np.concatenate([annotation, mirror_annotation], axis=0)
                    image = image[:step]
                    annotation = annotation[:step]
            start, stop = get_start_stop(annotation, extension=0)
            image_features['image_path'] = image_path
            image_features['image'] = image
            image_features['annotation'] = annotation
            image_features['start'] = start
            image_features['stop'] = stop
            image_features['z_images'] = image.shape[0]
            image_features['rows'] = image.shape[1]
            image_features['cols'] = image.shape[2]
            image_features['spacing'] = spacing
            out_features['Image_{}'.format(index)] = image_features
            start_chop += step
        return out_features


class Distribute_into_2D(Image_Processor):
    def parse(self, input_features):
        out_features = {}
        image = input_features['image']
        annotation = input_features['annotation']
        image_path = input_features['image_path']
        spacing = input_features['spacing']
        z_images_base, rows, cols = annotation.shape
        for index in range(z_images_base):
            image_features = OrderedDict()
            image_features['image_path'] = image_path
            image_features['image'] = image[index]
            image_features['annotation'] = annotation[index]
            image_features['rows'] = rows
            image_features['cols'] = cols
            image_features['spacing'] = spacing[:-1]
            out_features['Image_{}'.format(index)] = image_features
        return out_features


class Normalize_to_annotation(Image_Processor):
    def __init__(self, annotation_value=None):
        '''
        :param annotation_value: mask value to normalize over
        '''
        assert annotation_value is not None, 'Need to provide a value'
        self.annotation_value = annotation_value

    def parse(self, input_features):
        images = input_features['image']
        annotation = input_features['annotation']
        data = images[annotation==self.annotation_value].flatten()
        counts, bins = np.histogram(data, bins=100)
        bins = bins[:-1]
        count_index = np.where(counts == np.max(counts))[0][-1]
        peak = bins[count_index]
        data_reduced = data[np.where((data > peak - 150) & (data < peak + 150))]
        counts, bins = np.histogram(data_reduced, bins=1000)
        bins = bins[:-1]
        count_index = np.where(counts == np.max(counts))[0][-1]
        half_counts = counts - np.max(counts) // 2
        half_upper = np.abs(half_counts[count_index + 1:])
        max_50 = np.where(half_upper == np.min(half_upper))[0][0]

        half_lower = np.abs(half_counts[:count_index - 1][-1::-1])
        min_50 = np.where(half_lower == np.min(half_lower))[0][0]

        min_values = bins[count_index - min_50]
        max_values = bins[count_index + max_50]
        data = data[np.where((data >= min_values) & (data <= max_values))]
        mean_val, std_val = np.mean(data), np.std(data)
        images = (images - mean_val) / std_val
        input_features['image'] = images
        return input_features


class Add_Bounding_Box_Indexes(Image_Processor):
    def __init__(self, wanted_vals_for_bbox=None):
        '''
        :param wanted_vals_for_bbox: a list of values in integer form for bboxes
        '''
        assert type(wanted_vals_for_bbox) is list, 'Provide a list for bboxes'
        self.wanted_vals_for_bbox=wanted_vals_for_bbox

    def parse(self, input_features):
        annotation = input_features['annotation']
        for val in self.wanted_vals_for_bbox:
            slices = np.where(annotation == val)
            if slices:
                bounding_boxes, volumes = get_bounding_boxes(sitk.GetImageFromArray(annotation), val)
                bounding_boxes = bounding_boxes[0]
                volumes = volumes[0]
                c_start, r_start, z_start, c_stop, r_stop, z_stop = bounding_boxes
                z_stop, r_stop, c_stop = z_start + z_stop, r_start + r_stop, c_start + c_stop
            input_features['bounding_boxes_z_start_{}'.format(val)] = z_start
            input_features['bounding_boxes_r_start_{}'.format(val)] = r_start
            input_features['bounding_boxes_c_start_{}'.format(val)] = c_start
            input_features['bounding_boxes_z_stop_{}'.format(val)] = z_stop
            input_features['bounding_boxes_r_stop_{}'.format(val)] = r_stop
            input_features['bounding_boxes_c_stop_{}'.format(val)] = c_stop
            input_features['volumes_{}'.format(val)] = volumes
        return input_features


if __name__ == '__main__':
    pass
