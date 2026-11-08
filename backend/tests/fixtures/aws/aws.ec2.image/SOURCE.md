# SOURCE: AWS::EC2::Image (AMI, native EC2 API only)

Native API: https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeImages.html (samples, text about deregistered images, `ExecutableBy`, `IncludeDeprecated`, `IncludeDisabled`)
Data type: https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_Image.html (member list, valid values for `State`/`Architecture`/`ImageType`...)

**No `config_item.json`, `cloudcontrol_get.json`, `cloudcontrol_list.json`.** Verified:
- Config: `AWS::EC2::Image` does not appear in https://docs.aws.amazon.com/config/latest/developerguide/resource-config-reference.html (0 matches) and there is no `AWS::EC2::Image.properties.json` (HTTP 404) in the
  Config schema repo (https://raw.githubusercontent.com/awslabs/aws-config-resource-schema/master/config/properties/resource-types/).
- Cloud Control: the CloudFormation schema zip (https://schema.cloudformation.us-east-1.amazonaws.com/CloudformationSchema.zip) has no `aws-ec2-image.json`; the only image-like EC2 files are
  `aws-ec2-fpgaimage.json` and `aws-ec2-snapshot.json`.

## Shape of the four files
They are boto3-style `describe_images` responses (`{"Images": [...]}`, no `ResponseMetadata`, matching the brief's
`{"Images": []}` for the empty case). boto3/botocore member names are the PascalCase form of the API members; two differ from the XML
element names in the API reference: `imageState` -> `State`, `isPublic` -> `Public`, `imageOwnerId` -> `OwnerId`, `blockDeviceMapping` -> `BlockDeviceMappings`,
`tagSet` -> `Tags`. (The Example 1 XML sample itself uses `ownerId`/`public`/`blockDeviceMapping`, inconsistent with the API_Image page which
documents `imageOwnerId`/`isPublic`; botocore uses `OwnerId`/`Public`.) The botocore model is not one of the allowed sources, so this renaming
is the one place where a name is not copied from a page: it is the standard botocore mapping.

## describe_images_self.json (`Owners=["self"]`)
- Image `ami-1234567890EXAMPLE` copied from DescribeImages Example 1 (RHEL-8.0.0 image: `VirtualizationType hvm`,
  `Description "Provided by Red Hat, Inc."`, `PlatformDetails`, `EnaSupport true`, `Hypervisor xen`, `State available`, `SriovNetSupport simple`,
  `UsageOperation RunInstances:0010`, one EBS mapping `snap-1234567890abcdef0`/15/standard, `Architecture x86_64`, `RootDeviceType ebs`,
  `RootDeviceName /dev/sda1`, `CreationDate 2019-05-10T13:17:12.000Z`, `Public true`, `ImageType machine`, `Name`, empty `Tags`).
- Edits: `OwnerId 123456789012` -> `111122223333` and `ImageLocation` prefix changed to the same account. `Public: true` is kept from the sample
  (odd for a self-owned image, but verbatim).

## describe_images_shared.json (`ExecutableUsers=["self"]`, image owned by `444455556666`)
- No sample exists for a shared image. Built from the Example 1 structure with documented members only: `ImageId ami-1a2b3c4d`
  (the ID used in Examples 2 and 3), `OwnerId 444455556666`, `Public false`, `State available`, `Architecture x86_64`, `ImageType machine`,
  `RootDeviceType ebs`, `RootDeviceName /dev/sda1`, an EBS mapping (`snap-1234567890abcdef0`, 8, `DeleteOnTermination true`, from Example 3),
  `VirtualizationType hvm`, `Hypervisor xen`, `EnaSupport true`, `PlatformDetails Linux/UNIX`, `UsageOperation RunInstances`, `CreationDate` of Example 1.
- Invented values: `Name example-shared-ami.1` (pattern of Example 3's `example-marketplace-amzn-ami.1`), `Description`, `ImageLocation`.
- Shared via org: https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeImages.html says `ExecutableBy` = `self` returns AMIs shared with the account and AMIs
  shared with its organization/OU. The response itself does not tell how it was shared, so nothing in the file encodes that.

## describe_images_by_id_deregistered.json (`ImageIds=[...]`)
- Same structure as the owned image with `ImageId ami-bff32ccc` (ID from the DescribeInstances sample), `State "deregistered"`
  (documented valid value), `Public false`. The doc says "Recently deregistered images appear in the returned results for a short interval".
  `Name`, `Description`, `ImageLocation` are invented.

## describe_images_by_id_empty.json
- `{"Images": []}`, the "then return empty results" case from the same page.
- The page also says that later "specifying the ID of the image will eventually return an error indicating that the AMI ID cannot be found".
  The page does not print the error code (`InvalidAMIID.NotFound`); that code is not confirmed by an allowed source here, so there is no error fixture.

## ARN
The AMI ARN template in the spec (`arn:{partition}:ec2:{region}::image/{id}`) is not shown on the DescribeImages page; not verified from an allowed source.
