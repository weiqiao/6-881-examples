import numpy as np
import meshcat.geometry as g
import meshcat.transformations as tf
from scipy.optimize import fmin_bfgs
from sklearn.neighbors import NearestNeighbors


# TODO(kmuhlrad): move to separate library
def MakeMeshcatColorArray(N, r, g, b):
    """Constructs a color array to visualize a point cloud in meshcat.

    @param N int. Number of points to generate. Must be >= number of points in
        the point cloud to color.
    @param r float. The red value of the points, 0.0 <= r <= 1.0.
    @param g float. The green value of the points, 0.0 <= g <= 1.0.
    @param b float. The blue value of the points, 0.0 <= b <= 1.0.

    @return 3xN numpy array of the same color.
    """
    color = np.zeros((3, N))
    color[0, :] = r
    color[1, :] = g
    color[2, :] = b

    return color.T
  

def PlotMeshcatPointCloud(meshcat_vis, point_cloud_name, points, colors):
    """A wrapper function to plot meshcat point clouds.

    Args:
    @param meshcat_vis An instance of a meshcat visualizer.
    @param point_cloud_name string. The name of the meshcat point clouds.
    @param points An Nx3 numpy array of (x, y, z) points.
    @param colors An Nx3 numpy array of (r, g, b) colors corresponding to
        points.
    """

    meshcat_vis[point_cloud_name].set_object(g.PointCloud(points.T, colors.T))

def VisualizeTransform(meshcat_vis, points, transform):
    """Visualizes the points transformed by transform in yellow.

    Args:
    @param meschat_vis An instance of a meshcat visualizer.
    @param points An Nx3 numpy array representing a point cloud.
    @param transform a 4x4 numpy array representing a homogeneous
        transformation.
    """

    # Make meshcat color arrays.
    N = points.shape[0]

    yellow = MakeMeshcatColorArray(N, 1, 1, 0)

    homogenous_points = np.ones((N, 4))
    homogenous_points[:, :3] = np.copy(points)

    transformed_points = transform.dot(homogenous_points.T)

    transformed_points_meshcat = \
        g.PointCloud(transformed_points[:3, :], yellow.T)

    meshcat_vis['transformed_observations'].set_object(
        transformed_points_meshcat)


def ThresholdArray(arr, min_val, max_val):
    """
    Finds where the values of arr are between min_val and max_val (inclusive).

    @param arr An (N, ) numpy array containing number values.
    @param min_val number. The minimum value threshold.
    @param max_val number. The maximum value threshold.

    @return An (M, ) numpy array of the integer indices in arr with values that
        are between min_val and max_val.
    """
    return np.where(
        abs(arr - (max_val + min_val) / 2.) < (max_val - min_val) / 2.)[0]

def PoseToTransform(pose):
    """
    Puts an (x, y, sin(theta), cos(theta)) pose into a 4x4 homogenous
    transformation matrix with a translation by x and y and rotation by theta
    about the z-axis.

    @param pose A (4,) numpy matrix containing an (x, y, sin(theta), cos(theta))
            transform.

    @return transform A 4x4 numpy matrix representing the homogenous
        transformation from pose.

        transform = [cos(theta), -sin(theta),   0,    x]
                    [sin(theta),  cos(theta),   0,    y]
                    [0,           0,            1,    0]
                    [0,           0,            0,    1]
    """
    transform = np.eye(4)
    x, y, sin_th, cos_th = pose

    transform[0, 3] = x
    transform[1, 3] = y
    transform[0, 0] = cos_th
    transform[1, 1] = cos_th
    transform[1, 0] = sin_th
    transform[0, 1] = -sin_th

    return transform


def TransformToPose(transform):
    """
    Takes the pose parameters from a 4x4 homogenous transformation matrix with a
    translation by x and y and rotation ab rotation by theta about the z-axis.

    @param transform: A 4x4 numpy matrix representing a homogenous
        transformation.

        transform = [cos(theta), -sin(theta),   0,    x]
                    [sin(theta),  cos(theta),   0,    y]
                    [0,           0,            1,    0]
                    [0,           0,            0,    1]

    @return A (4,) numpy array with the pose information in the order
        (x, y, sin(theta), cos(theta)).
    """
    return np.array([transform[0, 3],
                     transform[1, 3],
                     transform[1, 0],
                     transform[0, 0]])


def FindBestFitTransform(scene_points, model_points, init_guess, max_distance):
    """
    Finds the best fit (x, y, sin(theta), cos(theta)) values that map
    scene_points to model_points given an initial guess.

    @param scene_points An Nx4 numpy array of homogenous points in the scene.
    @param model_points An Nx4 numpy array of homogenous points in the model.
    @param init_guess A (4,) numpy array of an (x, y, sin(theta), cos(theta))
        guess.
    @param max_distance float. The maximum distance in meters to consider of
            nearest neighbors between scene_points and model_points.

    @return X_MS A 4x4 numpy array of the best-fit homogenous transformation
        between scene_points and model_points.
    @return cost float. The cost function evaluated at X_MS.
    """
    X_MS = np.eye(4)
    nn = NearestNeighbors(
            n_neighbors=1, algorithm='kd_tree').fit(model_points.T[:, :3])
    
    def CostFunction(pose_vector):
        """The objective to minimize.

        Args:
        @param pose_vector A (4,) numpy array of an (x, y, sin_th, cos_th) pose.

        Returns:
        @return float. the value of the objective at pose_vector
        """
        x, y, sin_th, cos_th = pose_vector
        X_MS = PoseToTransform(pose_vector)

        cost = 0

        transformed_scene = np.dot(X_MS, scene_points)
        angle_penalty = (cos_th*cos_th + sin_th*sin_th) - 1.0
        distances, _ = nn.kneighbors(transformed_scene.T[:, :3])
        dist_cost = np.sum(np.clip(distances, 0., max_distance))
        dist_cost /= transformed_scene.shape[1]
        cost = dist_cost + 100*angle_penalty**2

        return cost
    
    init_pose = TransformToPose(init_guess)
    init_cost = CostFunction(init_pose)

    ans = fmin_bfgs(CostFunction, init_pose, disp=0, full_output=True)
    pose, cost = ans[0:2]

    X_MS = PoseToTransform(pose)
    return X_MS, cost


def AlignSceneToModel(scene_points, model_points, max_distance=0.05,
                      num_iters=10, num_sample_points=250):
    """
    Finds a good (x, y, sin(theta), cos(theta) transform between scene_points
    and model_points.

    Args:
    @param scene_points An Nx3 numpy array of scene points.
    @param model_points An Nx3 numpy array of model points.
    @param max_distance float. The maximum distance in meters to consider of
        nearest neighbors between scene_points and model_points.
    @param num_iters int. The number of times to try the best fit optimization
        from different initial guesses.
    @param num_sample_points: int. The number of points to sample from the scene
        to limit the time the algorithm takes to run.

    Returns:
    @return best_X_MS The best 4x4 homogenous transform between scene_points and
        model_points.
    @return best_cost float. The cost function evaluated at best_X_MS.
    """
    scene_sample = scene_points[np.random.choice(scene_points.shape[0],
                                                 num_sample_points,
                                                 replace=False),
                                :]

    homogenous_scene = np.ones((4, scene_sample.shape[0]))
    homogenous_model = np.ones((4, model_points.shape[0]))

    homogenous_scene[:3, :] = scene_sample.T
    homogenous_model[:3, :] = model_points.T

    centroid_scene = np.mean(homogenous_scene, axis=1)
    centroid_model = np.mean(homogenous_model, axis=1)

    best_X_MS = None
    best_cost = float('inf')

    for i in range(num_iters):
        init_theta = np.random.uniform(0, 2*np.pi)
        
        init_guess = reduce(np.dot,
                           [tf.translation_matrix((centroid_model[0],
                                                   centroid_model[1],
                                                   0.)),
                            tf.rotation_matrix(init_theta, (0, 0, 1)),
                            tf.translation_matrix((-centroid_scene[0],
                                                   -centroid_scene[1],
                                                   0.))])
        
        X_MS, cost = FindBestFitTransform(
            homogenous_scene,homogenous_model, init_guess, max_distance)
        
        if cost < best_cost:
            best_cost = cost
            best_X_MS = X_MS

    return best_X_MS, best_cost