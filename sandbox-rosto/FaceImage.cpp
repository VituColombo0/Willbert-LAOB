/***********************************************************************
FaceImage - Foto (rosto) projetada sobre a areia do AR Sandbox.
Parte do Willbert (modo Rosto).
***********************************************************************/

#include "FaceImage.h"

#include <GL/gl.h>
#include <GL/GLContextData.h>
#include <GL/Extensions/GLARBShaderObjects.h>
#include <Images/ReadImageFile.h>

FaceImage::DataItem::DataItem(void)
	:textureObjectId(0)
	{
	GLARBShaderObjects::initExtension();
	glGenTextures(1,&textureObjectId);
	}

FaceImage::DataItem::~DataItem(void)
	{
	glDeleteTextures(1,&textureObjectId);
	}

FaceImage::FaceImage(const char* imageFileName)
	:GLObject(false),
	 image(Images::readImageFile(imageFileName)),
	 reference(0.0f)
	{
	for(int i=0;i<16;++i)
		faceTransformMatrix[i]=(i%5==0)?1.0f:0.0f;
	params[0]=0.03f; // 10 cm de morro = 30% de ampliação
	params[1]=0.0f;
	params[2]=0.0f; // 0 = só a foto, sem as cores de relevo
	params[3]=1.0f;
	
	/* Só registra no OpenGL depois que a foto foi lida: */
	GLObject::init();
	}

void FaceImage::initContext(GLContextData& contextData) const
	{
	DataItem* dataItem=new DataItem;
	contextData.addDataItem(this,dataItem);
	
	glBindTexture(GL_TEXTURE_2D,dataItem->textureObjectId);
	glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_LINEAR);
	glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_LINEAR);
	glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_S,GL_CLAMP_TO_EDGE);
	glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_T,GL_CLAMP_TO_EDGE);
	glPixelStorei(GL_UNPACK_ALIGNMENT,1);
	image.glTexImage2D(GL_TEXTURE_2D,0,GL_RGB8);
	glBindTexture(GL_TEXTURE_2D,0);
	}

namespace {

/* Multiplica matrizes 4x4 linha-major: r = a*b */
void mul(const double a[4][4],const double b[4][4],double r[4][4])
	{
	for(int i=0;i<4;++i)
		for(int j=0;j<4;++j)
			{
			r[i][j]=0.0;
			for(int k=0;k<4;++k)
				r[i][j]+=a[i][k]*b[k][j];
			}
	}

void ident(double m[4][4])
	{
	for(int i=0;i<4;++i)
		for(int j=0;j<4;++j)
			m[i][j]=i==j?1.0:0.0;
	}

}

void FaceImage::setTransform(const OGTransform& cameraToBox,const double boxMin[2],const double boxMax[2],int rotation,bool mirror)
	{
	/* Câmera -> caixa (cm, z = altura acima do plano base): */
	PTransform pb(cameraToBox);
	double box[4][4];
	for(int i=0;i<4;++i)
		for(int j=0;j<4;++j)
			box[i][j]=pb.getMatrix()(i,j);
	
	/* Centro da caixa na origem: */
	double center[4][4];
	ident(center);
	center[0][3]=-(boxMin[0]+boxMax[0])*0.5;
	center[1][3]=-(boxMin[1]+boxMax[1])*0.5;
	
	/* Giro (múltiplos de 90°) e espelho, em cm: */
	int r=((rotation%360)+360)%360;
	double c=r==0?1.0:(r==180?-1.0:0.0);
	double s=r==90?1.0:(r==270?-1.0:0.0);
	double rot[4][4];
	ident(rot);
	rot[0][0]=c;rot[0][1]=-s;
	rot[1][0]=s;rot[1][1]=c;
	if(mirror)
		{
		rot[0][0]=-rot[0][0];
		rot[0][1]=-rot[0][1];
		}
	
	/* Tamanho da foto na areia: o maior que cabe na caixa sem esticar: */
	double bw=boxMax[0]-boxMin[0],bh=boxMax[1]-boxMin[1];
	double extX=(r==90||r==270)?bh:bw,extY=(r==90||r==270)?bw:bh;
	double imgAspect=double(image.getWidth())/double(image.getHeight());
	double H=extY<extX/imgAspect?extY:extX/imgAspect;
	double W=H*imgAspect;
	double scale[4][4];
	ident(scale);
	scale[0][0]=1.0/W;scale[0][3]=0.5;
	scale[1][1]=1.0/H;scale[1][3]=0.5;
	
	double t1[4][4],t2[4][4],full[4][4];
	mul(center,box,t1);
	mul(rot,t1,t2);
	mul(scale,t2,full);
	
	GLfloat* p=faceTransformMatrix;
	for(int j=0;j<4;++j)
		for(int i=0;i<4;++i,++p)
			*p=GLfloat(full[i][j]);
	}

void FaceImage::bindTexture(GLContextData& contextData) const
	{
	DataItem* dataItem=contextData.retrieveDataItem<DataItem>(this);
	glBindTexture(GL_TEXTURE_2D,dataItem->textureObjectId);
	}

void FaceImage::uploadUniforms(GLint transformLocation,GLint paramsLocation,GLint referenceLocation) const
	{
	glUniformMatrix4fvARB(transformLocation,1,GL_FALSE,faceTransformMatrix);
	glUniform4fvARB(paramsLocation,1,params);
	glUniform1fARB(referenceLocation,reference);
	}
